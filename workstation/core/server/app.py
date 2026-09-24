"""本地工作台 HTTP 服务：路由 + 装配 + loopback 守门。

端点（全部同源，无 CORS —— 页面本身就由本进程提供，跨域无处可去）：

    GET  /                       工作台视图（单文件静态页）
    GET  /api/health             版本 / home / 已知场景 / 已注册技能
    GET  /api/skills             技能 manifest 列表
    GET  /api/runs               历史 Run（?status=&skill=&limit=）
    POST /api/runs               提交 Run：{skill, inputs, context, options?}
    GET  /api/runs/<id>          单个 Run 详情
    GET  /api/runs/<id>/events   事件回放（SSE，RunEvent 即前端契约）
    POST /api/retrieve           按场景检索：{query, context, limit, cross_context}
"""

from __future__ import annotations

import json
import re
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from workstation import __version__ as WS_VERSION
from workstation_contracts import CONTRACT_VERSION, RunStatus, TaskOptions, TaskRequest
from workstation.core.retrieval import RetrievalService
from workstation.core.retrieval.source import Hit
from workstation.core.retrieval.sources import (
    KnowledgeRetrievalSource,
    MarkdownFolderSource,
    PdfFolderSource,
)
from workstation.core.runtime import (
    SkillRuntime,
    RunStore,
    open_run_store,
    to_sse,
)
from workstation.core.runtime.runtime import Skill
from workstation.skills.knowledge import (
    KNOWLEDGE_SKILL_NAME,
    KnowledgeSkill,
    KnowledgeSkillConfig,
)
from workstation.skills.ppt import (
    PPT_SKILL_NAME,
    PptSkill,
    PptSkillConfig,
    SubprocessRunner,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: 唯一允许的绑定地址。写成白名单而非"检查是否 0.0.0.0"，
#: 因为漏一个 IPv6 / 网卡地址就等于把本机数据放上局域网。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "127.0.0.0/8"})

DEFAULT_PORT = 8787


class ServerError(RuntimeError):
    """服务装配或守门失败。"""


# --------------------------------------------------------------------- 装配


@dataclass
class Workbench:
    """一次进程内的工作台装配：落盘 + 技能编排 + 检索门面。"""

    home: Path
    store: RunStore
    runtime: SkillRuntime
    retrieval: RetrievalService
    contexts: list[str]

    @property
    def url_stub(self) -> str:
        return f"home={self.home}"


def build_workbench(
    *,
    home: str | Path,
    ppt_agent: str | Path,
    knowledge_root: str | Path,
    vault: str | None = None,
    markdown_folder: str | None = None,
    pdf_folder: str | None = None,
) -> Workbench:
    """装配工作台。所有路径解析与 CLI 保持一致，但由调用方显式给出。"""
    home = Path(home).expanduser()
    home.mkdir(parents=True, exist_ok=True)

    store = open_run_store(home)
    runtime = SkillRuntime(
        store,
        {
            PPT_SKILL_NAME: PptSkill(
                PptSkillConfig(workspace_home=home, ppt_agent_root=Path(ppt_agent)),
                SubprocessRunner(),
            ),
            KNOWLEDGE_SKILL_NAME: KnowledgeSkill(
                KnowledgeSkillConfig(workspace_home=home, knowledge_root=Path(knowledge_root)),
                SubprocessRunner(),
            ),
        },
    )

    retrieval = RetrievalService()
    contexts: list[str] = []

    kb_src = KnowledgeRetrievalSource(
        knowledge_root=Path(knowledge_root),
        vault=vault or _default_vault(Path(knowledge_root)),
    )
    retrieval.register(kb_src)
    contexts.extend(kb_src.contexts)

    md_src = MarkdownFolderSource(markdown_folder or str(_REPO / "examples" / "fixtures" / "markdown"))
    retrieval.register(md_src)
    contexts.extend(md_src.contexts)

    pdf_src = PdfFolderSource(pdf_folder or str(_REPO / "examples" / "fixtures" / "pdfs"))
    retrieval.register(pdf_src)
    contexts.extend(pdf_src.contexts)

    return Workbench(
        home=home,
        store=store,
        runtime=runtime,
        retrieval=retrieval,
        contexts=_dedupe(contexts),
    )


_REPO = Path(__file__).resolve().parents[3]


def _default_vault(kb: Path) -> str:
    cand = kb / "knowledge"
    return str(cand) if cand.exists() else str(kb)


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for it in items:
        if it not in seen:
            seen.append(it)
    return seen


# --------------------------------------------------------------------- 序列化


def _hit_dict(h: Hit) -> dict[str, Any]:
    return {
        "ref": h.ref,
        "source": h.source,
        "doc_id": h.doc_id,
        "title": h.title,
        "text": h.text,
        "score": round(h.score, 4),
        "page": h.page,
        "uri": h.uri,
        "section": h.section,
    }


def _skill_dict(skill: Skill) -> dict[str, Any]:
    manifest = skill.manifest
    return manifest.model_dump(mode="json")


# --------------------------------------------------------------------- 处理


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = f"workstation/{WS_VERSION}"
    protocol_version = "HTTP/1.1"

    # 让默认日志安静一点：本机工具的访问日志没有价值，且会污染终端。
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D102
        return

    # -- helpers ---------------------------------------------------------

    @property
    def wb(self) -> Workbench:
        return self.server.workbench  # type: ignore[attr-defined]

    def _send(self, code: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _fail(self, code: int, kind: str, message: str) -> None:
        self._json(code, {"error": kind, "message": message})

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"请求体不是合法 JSON：{exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return parsed

    def _serve_static(self, rel: str) -> None:
        # 防目录穿越：解析后必须仍落在 STATIC_DIR 内。
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())):
            self._fail(403, "forbidden", "静态路径越界")
            return
        if not target.is_file():
            self._fail(404, "not_found", f"静态资源不存在：{rel}")
            return
        ctype = "text/html; charset=utf-8" if target.suffix == ".html" else "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    # -- GET -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/") :])
            return

        if path == "/api/health":
            self._json(
                200,
                {
                    "status": "ok",
                    "version": WS_VERSION,
                    "contract_version": CONTRACT_VERSION,
                    "home": str(self.wb.home),
                    "contexts": self.wb.contexts,
                    "skills": sorted(self.wb.runtime.skills),
                    "sources": self.wb.retrieval.sources,
                },
            )
            return

        if path == "/api/skills":
            self._json(200, {"skills": [_skill_dict(s) for s in self.wb.runtime.skills.values()]})
            return

        if path == "/api/runs":
            self._json(200, {"runs": [self._run_brief(r) for r in self._list_runs(query)]})
            return

        m = re.fullmatch(r"/api/runs/([^/]+)/events", path)
        if m:
            self._events(m.group(1))
            return

        m = re.fullmatch(r"/api/runs/([^/]+)", path)
        if m:
            self._run_detail(m.group(1))
            return

        self._fail(404, "not_found", f"未知路径：{path}")

    def _list_runs(self, query: dict[str, list[str]]) -> list[Any]:
        kwargs: dict[str, Any] = {"limit": int(_one(query, "limit") or 20)}
        status = _one(query, "status")
        if status:
            try:
                kwargs["status"] = RunStatus(status)
            except ValueError:
                raise ValueError(f"未知状态：{status}") from None
        skill = _one(query, "skill")
        if skill:
            kwargs["skill"] = skill
        return self.wb.runtime.list(**kwargs)

    def _run_brief(self, run: Any) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "skill": run.skill,
            "status": run.status.value,
            "context": getattr(run, "context", "default"),
            "steps": len(run.steps),
            "cost_cny": round(run.usage.cost_cny, 6),
            "llm_calls": run.usage.llm_calls,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "error": run.error,
        }

    def _run_detail(self, run_id: str) -> None:
        run = self.wb.runtime.get(run_id)
        if run is None:
            self._fail(404, "not_found", f"无此 run：{run_id}")
            return
        payload = run.model_dump(mode="json")
        payload["events_available"] = True
        self._json(200, payload)

    def _events(self, run_id: str) -> None:
        run = self.wb.runtime.get(run_id)
        if run is None:
            self._fail(404, "not_found", f"无此 run：{run_id}")
            return
        body = to_sse(self.wb.runtime.events(run_id)).encode("utf-8")
        self._send(
            200,
            body,
            "text/event-stream; charset=utf-8",
            {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- POST ------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            payload = self._read_body()
        except ValueError as exc:
            self._fail(400, "bad_request", str(exc))
            return

        if path == "/api/runs":
            self._submit(payload)
            return
        if path == "/api/retrieve":
            self._retrieve(payload)
            return
        self._fail(404, "not_found", f"未知路径：{path}")

    def _submit(self, payload: dict[str, Any]) -> None:
        skill = payload.get("skill")
        if not skill:
            self._fail(400, "bad_request", "缺少 skill")
            return
        if skill not in self.wb.runtime.skills:
            self._fail(
                400,
                "unknown_skill",
                f"未知技能：{skill}（可用：{', '.join(sorted(self.wb.runtime.skills))}）",
            )
            return
        opts = payload.get("options") or {}
        try:
            options = TaskOptions(**opts) if isinstance(opts, dict) else TaskOptions()
            request = TaskRequest(
                skill=skill,
                inputs=payload.get("inputs") or {},
                options=options,
                context=payload.get("context", "default"),
            )
        except Exception as exc:  # noqa: BLE001 - 契约校验失败的细节要回给调用方
            self._fail(400, "bad_request", f"请求不合契约：{exc}")
            return

        try:
            run = self.wb.runtime.submit(request)
        except Exception as exc:  # noqa: BLE001
            self._fail(500, "run_failed", str(exc))
            return
        code = 200 if run.status is RunStatus.SUCCEEDED else 422
        self._json(code, run.model_dump(mode="json"))

    def _retrieve(self, payload: dict[str, Any]) -> None:
        query = payload.get("query")
        if not query:
            self._fail(400, "bad_request", "缺少 query")
            return
        context = payload.get("context", "default")
        limit = int(payload.get("limit") or 10)
        cross = bool(payload.get("cross_context", False))

        result = self.wb.retrieval.retrieve(
            query, context=context, limit=limit, cross_context=cross
        )
        self._json(
            200,
            {
                "query": result.query,
                "context": result.context,
                "cross_context": result.cross_context,
                "merged": result.merged,
                "sources": {k: [_hit_dict(h) for h in v] for k, v in result.per_source.items()},
                "hits": [_hit_dict(h) for h in result.hits],
            },
        )


def _one(query: dict[str, list[str]], key: str) -> str | None:
    vals = query.get(key)
    return vals[0] if vals else None


# --------------------------------------------------------------------- 启动


def create_server(
    workbench: Workbench,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
) -> ThreadingHTTPServer:
    """创建服务。**守门**：非 loopback 一律拒绝。

    ``port=0`` 交给内核分配空闲端口（测试用），实际端口读
    ``server.server_address[1]``。
    """
    if host not in LOOPBACK_HOSTS:
        raise ServerError(
            f"拒绝绑定到非 loopback 地址：{host}。"
            f"允许：{', '.join(sorted(LOOPBACK_HOSTS))} —— 数据不出本机是硬约束。"
        )
    httpd = ThreadingHTTPServer((host, port), WorkbenchHandler)
    httpd.workbench = workbench  # type: ignore[attr-defined]
    return httpd


def serve(
    workbench: Workbench,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    *,
    open_browser: bool = False,
) -> None:
    """启动并阻塞。Ctrl-C 退出。"""
    httpd = create_server(workbench, host=host, port=port)
    actual = httpd.server_address[1]
    url = f"http://{'localhost' if host == '::1' else host}:{actual}/"
    print(f"workstation {WS_VERSION} · 工作台已启动：{url}")
    print(f"home={workbench.home}  contexts={','.join(workbench.contexts)}")
    print("（仅监听 loopback；Ctrl-C 停止）")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 无浏览器环境时静默降级
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()
        workbench.store.close()
