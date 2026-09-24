"""`python -m workstation.cli` —— 当前阶段唯一可启动入口。

提供子命令：

    python -m workstation.cli doctor            # 检查本机环境能否真正跑起来
    python -m workstation.cli run ppt --input examples/fixtures/ppt_request.json
    python -m workstation.cli run knowledge --input examples/fixtures/knowledge_request.json
    python -m workstation.cli retrieve --query "..." --context interview
    python -m workstation.cli pdf extract <file.pdf> [--json]
    python -m workstation.cli serve                  # 阶段 5：本地工作台视图
    python -m workstation.cli runs list
    python -m workstation.cli runs show <run_id>

说明：本项目**没有**定时任务、没有对外服务。它是一套「库 + 契约 + 技能 +
检索门面 + 一个只监听 loopback 的本地视图」。这个 CLI 是把「能用」这件事
落地的最小入口。检索（``retrieve``）按 context 收窄（红线#5：记忆不合并）；
``pdf`` 是本地 PDF 解析工具，填补知识库的 PDF 缺口；``serve`` 起的是本机
工作台，**非 loopback 地址一律拒绝绑定**（数据不出本机）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - 非 UTF-8 终端时跳过
    pass

from workstation import __version__ as WS_VERSION
from workstation_contracts import CONTRACT_VERSION
from workstation_contracts import RunStatus, TaskOptions, TaskRequest
from workstation.core.runtime import SkillRuntime, open_run_store
from workstation.core.server import ServerError, build_workbench, serve
from workstation.core.retrieval import RetrievalService
from workstation.core.retrieval.sources import (
    KnowledgeRetrievalSource,
    MarkdownFolderSource,
    PdfFolderSource,
)
from workstation.skills.ppt import (
    PPT_SKILL_NAME,
    PptSkill,
    PptSkillConfig,
    SubprocessRunner,
)
from workstation.skills.knowledge import (
    KNOWLEDGE_SKILL_NAME,
    KnowledgeSkill,
    KnowledgeSkillConfig,
)
from workstation.tools.pdf import cli as pdf_cli

REPO = Path(__file__).resolve().parents[1]
DEFAULT_HOME = REPO / "runtime"
DEFAULT_PPT_AGENT = Path("D:/develop/project/PPTagent")  # 仅 fallback，env/yaml 优先
DEFAULT_KNOWLEDGE_ROOT = Path("D:/develop/agent for obsidian")  # 仅 fallback，env/yaml 优先

# 可选 YAML 配置：有 pyyaml 就读 config/workstation.yaml，否则忽略。
try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


def _load_yaml() -> dict:
    if yaml is None:
        return {}
    for p in (REPO / "config" / "workstation.yaml", REPO / "config" / "workstation.example.yaml"):
        if p.exists():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


_CFG = _load_yaml()


# ----------------------------------------------------------------- 配置解析


def resolve_home(args: argparse.Namespace) -> Path:
    raw = getattr(args, "home", None) or os.environ.get("WORKSTATION_HOME") or _CFG.get("home") or str(DEFAULT_HOME)
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def resolve_ppt_agent(args: argparse.Namespace) -> Path:
    raw = getattr(args, "ppt_agent", None) or os.environ.get("WORKSTATION_PPT_AGENT")
    if not raw and isinstance(_CFG.get("external"), dict):
        raw = _CFG["external"].get("ppt_agent")
    if not raw and isinstance(_CFG.get("skills"), dict):
        ppt = _CFG["skills"].get("ppt-deck") or {}
        if isinstance(ppt, dict):
            raw = ppt.get("repo")
    if not raw:
        raw = str(DEFAULT_PPT_AGENT)
    return Path(raw).expanduser()


def resolve_knowledge_root(args: argparse.Namespace) -> Path:
    raw = getattr(args, "knowledge_root", None) or os.environ.get("WORKSTATION_KNOWLEDGE_ROOT")
    if not raw and isinstance(_CFG.get("external"), dict):
        raw = _CFG["external"].get("knowledge")
    if not raw and isinstance(_CFG.get("skills"), dict):
        kb = _CFG["skills"].get("knowledge-search") or {}
        if isinstance(kb, dict):
            raw = kb.get("repo")
    if not raw:
        raw = str(DEFAULT_KNOWLEDGE_ROOT)
    return Path(raw).expanduser()


# ----------------------------------------------------------------- 诊断


def _row(tag: str, name: str, detail: str = "") -> bool:
    print(f"[{tag:4}] {name}" + (f"  — {detail}" if detail else ""))
    return tag in ("ok  ", "warn")


def cmd_doctor(args: argparse.Namespace) -> int:
    print(f"workstation {WS_VERSION} · contracts {CONTRACT_VERSION}\n")
    ok = True

    ok &= _row("ok  ", f"python {sys.version.split()[0]}")

    home = resolve_home(args)
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".ws-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok &= _row("ok  ", f"home 可写: {home}")
    except Exception as exc:  # noqa: BLE001
        ok &= _row("FAIL", f"home 不可写: {home}", str(exc))

    try:
        store = open_run_store(home)
        store.close()
        ok &= _row("ok  ", f"run store: {home / 'derived' / 'runs.sqlite3'}")
    except Exception as exc:  # noqa: BLE001
        ok &= _row("FAIL", "run store 无法打开", str(exc))

    node = shutil.which("node")
    npx = shutil.which("npx")
    ok &= _row("ok  " if node else "FAIL", f"node: {node or '未找到'}")
    ok &= _row("ok  " if npx else "FAIL", f"npx : {npx or '未找到'}")

    ppt = resolve_ppt_agent(args)
    ok &= _row("ok  " if ppt.exists() else "FAIL", f"PPTAgent 根: {ppt}")
    cli_ts = ppt / "src" / "cli.ts"
    ok &= _row("ok  " if cli_ts.exists() else "FAIL", f"cli 入口: {cli_ts}" + ("" if cli_ts.exists() else "（缺失）"))
    ok &= _row("ok  " if (ppt / "node_modules").exists() else "warn",
               "PPTAgent 依赖已装" if (ppt / "node_modules").exists() else "PPTAgent 依赖未装（先 cd PPTAgent && npm i）")
    ok &= _row("ok  " if (ppt / "spec" / "demo.deck.json").exists() else "warn",
               "demo deck 模板存在" if (ppt / "spec" / "demo.deck.json").exists() else "demo deck 模板缺失")

    key = os.environ.get("WORKSTATION_DEEPSEEK_API_KEY")
    # render 模式不需要 key；plan 模式（让 LLM 生成 DeckSpec）才需要。
    ok &= _row("ok  " if key else "warn",
               "WORKSTATION_DEEPSEEK_API_KEY 已设置" if key else "未设置（render 模式够用；plan 模式需设置）")

    kb = resolve_knowledge_root(args)
    ok &= _row("ok  " if kb.exists() else "FAIL", f"知识库根: {kb}")
    # 知识库桥接复用其 esbuild devDep（无 tsx），检查 esbuild 是否可达即可。
    esbuild_bin = kb / "node_modules" / ".bin" / "esbuild"
    npm = shutil.which("npm")
    ok &= _row("ok  " if esbuild_bin.exists() else "warn",
               "知识库 esbuild 可用" if esbuild_bin.exists() else "知识库 node_modules 未装（先 cd 知识库 && npm i）")
    ok &= _row("ok  " if npm else "FAIL", f"npm: {npm or '未找到'}")
    ok &= _row("ok  " if (kb / "src" / "bridge.ts").exists() else "FAIL",
               "knowledge-bridge 入口存在" if (kb / "src" / "bridge.ts").exists() else "knowledge-bridge 入口缺失")

    if yaml is None and not (REPO / "config" / "workstation.yaml").exists():
        _row("warn", "未安装 pyyaml：config/workstation.yaml 不会被读取（CLI 参数/环境变量照常可用）")

    print()
    print("结论：环境" + ("就绪，可以跑 run ppt。" if ok else "存在阻塞项，先解决 FAIL。"))
    return 0 if ok else 1


# ----------------------------------------------------------------- 执行


def _build_input(data: dict, args: argparse.Namespace) -> dict:
    keys = ("brief", "facts", "material", "mode", "deck_spec", "requested_pages",
            "facts_per_page", "max_pages", "accept_padding", "theme")
    inputs = {k: data[k] for k in keys if k in data}
    if args.pages:
        inputs["requested_pages"] = args.pages
    if args.accept_padding:
        inputs["accept_padding"] = True
    return inputs


def _print_run(run) -> None:
    print(f"run_id   : {run.run_id}")
    print(f"status   : {run.status.value}")
    if run.error:
        print(f"error    : {run.error}")
    for art in run.artifacts:
        print(f"artifact : {art.uri}")
    print(f"cost     : ¥{run.usage.cost_cny:.4f}  llm_calls={run.usage.llm_calls}")
    print(f"source_ids: {run.source_ids}")
    if run.outputs.get("slide_count") is not None:
        print(f"slides   : {run.outputs['slide_count']}")


def _common_options(args: argparse.Namespace) -> TaskOptions:
    return TaskOptions(
        priority=args.priority,
        require_approval=args.require_approval,
        dry_run=args.dry_run,
        idempotency_key=args.idempotency,
    )


def _run_ppt(home: Path, ppt: Path, data: dict, args: argparse.Namespace) -> int:
    inputs = _build_input(data, args)
    request = TaskRequest(
        skill=PPT_SKILL_NAME,
        inputs=inputs,
        options=_common_options(args),
        context=getattr(args, "context", "default"),
    )
    config = PptSkillConfig(workspace_home=home, ppt_agent_root=ppt)
    store = open_run_store(home)
    runtime = SkillRuntime(store, {PPT_SKILL_NAME: PptSkill(config, SubprocessRunner())})
    run = runtime.submit(request)
    _print_run(run)
    return 0 if run.status is RunStatus.SUCCEEDED else 1


def _run_knowledge(home: Path, kb: Path, data: dict, args: argparse.Namespace) -> int:
    inputs: dict = {}
    if "vault" in data:
        inputs["vault"] = data["vault"]
    if args.vault:
        inputs["vault"] = args.vault
    if "query" in data:
        inputs["query"] = data["query"]
    if args.query:
        inputs["query"] = args.query
    if "limit" in data:
        inputs["limit"] = data["limit"]
    if args.limit:
        inputs["limit"] = args.limit
    if "scope" in data:
        inputs["scope"] = data["scope"]
    if not inputs.get("vault") or not inputs.get("query"):
        print("knowledge 输入必须含 vault（绝对路径）与 query。", file=sys.stderr)
        return 2

    request = TaskRequest(
        skill=KNOWLEDGE_SKILL_NAME,
        inputs=inputs,
        options=_common_options(args),
        context=getattr(args, "context", "default"),
    )
    config = KnowledgeSkillConfig(workspace_home=home, knowledge_root=kb)
    store = open_run_store(home)
    runtime = SkillRuntime(store, {KNOWLEDGE_SKILL_NAME: KnowledgeSkill(config, SubprocessRunner())})
    run = runtime.submit(request)
    _print_run(run)
    return 0 if run.status is RunStatus.SUCCEEDED else 1


def cmd_run(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8")) if getattr(args, "input", None) else {}
    except FileNotFoundError:
        print(f"找不到输入文件：{args.input}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"输入不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    if args.run_skill == "ppt":
        return _run_ppt(home, resolve_ppt_agent(args), data, args)
    if args.run_skill == "knowledge":
        return _run_knowledge(home, resolve_knowledge_root(args), data, args)
    print(f"未知技能子命令：{args.run_skill}", file=sys.stderr)
    return 2


def cmd_runs(args: argparse.Namespace) -> int:
    home = resolve_home(args)
    store = open_run_store(home)
    if args.runs_cmd == "list":
        kwargs: dict = {}
        if args.status:
            kwargs["status"] = args.status
        runs = store.list(**kwargs)[: args.limit]
        if not runs:
            print("（无历史 Run）")
            return 0
        for run in runs:
            print(f"{run.run_id}  {run.status.value:9}  skill={run.skill}  "
                  f"steps={len(run.steps)}  cost=¥{run.usage.cost_cny:.4f}")
        return 0
    if args.runs_cmd == "show":
        run = store.get(args.run_id)
        if run is None:
            print(f"无此 run：{args.run_id}", file=sys.stderr)
            return 2
        _print_run(run)
        if run.checkpoint_ref:
            print(f"checkpoint: {run.checkpoint_ref}  （进程崩溃可原位重放）")
        return 0
    return 2


def _default_vault(kb: Path) -> str:
    cand = kb / "knowledge"
    return str(cand) if cand.exists() else str(kb)


def cmd_retrieve(args: argparse.Namespace) -> int:
    """按场景隔离的跨源检索（阶段 4 门面）。"""
    kb = resolve_knowledge_root(args)
    vault = args.knowledge_vault or os.environ.get("WORKSTATION_KNOWLEDGE_VAULT") or _default_vault(kb)
    md = args.markdown_folder or os.environ.get("WORKSTATION_MARKDOWN_FOLDER") or str(
        REPO / "examples" / "fixtures" / "markdown"
    )
    pdf = args.pdf_folder or os.environ.get("WORKSTATION_PDF_FOLDER") or str(
        REPO / "examples" / "fixtures" / "pdfs"
    )

    service = RetrievalService()
    service.register(KnowledgeRetrievalSource(knowledge_root=kb, vault=vault))
    service.register(MarkdownFolderSource(md))
    service.register(PdfFolderSource(pdf))

    result = service.retrieve(
        args.query,
        context=args.context,
        limit=args.limit,
        cross_context=args.cross_context,
    )
    print(f"query   : {result.query}")
    print(f"context : {result.context}  cross_context={result.cross_context}  merged={result.merged}")
    print(f"sources : {', '.join(result.per_source) or '(none)'}")
    print(f"hits    : {len(result.hits)}")
    for i, h in enumerate(result.hits, 1):
        tail = f"  p{h.page}" if h.page else ""
        print(f"  [{i}] ({h.source}) {h.title}  score={h.score:.3f}{tail}")
        print(f"       {' '.join(str(h.text).split())[:140]}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """阶段 5：启动本地工作台视图（仅 loopback）。"""
    home = resolve_home(args)
    kb = resolve_knowledge_root(args)
    vault = args.knowledge_vault or os.environ.get("WORKSTATION_KNOWLEDGE_VAULT") or _default_vault(kb)
    md = args.markdown_folder or os.environ.get("WORKSTATION_MARKDOWN_FOLDER") or str(
        REPO / "examples" / "fixtures" / "markdown"
    )
    pdf = args.pdf_folder or os.environ.get("WORKSTATION_PDF_FOLDER") or str(
        REPO / "examples" / "fixtures" / "pdfs"
    )

    workbench = build_workbench(
        home=home,
        ppt_agent=resolve_ppt_agent(args),
        knowledge_root=kb,
        vault=vault,
        markdown_folder=md,
        pdf_folder=pdf,
    )
    try:
        serve(workbench, host=args.host, port=args.port, open_browser=args.open)
    except ServerError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


# ----------------------------------------------------------------- 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ws", description="个人 AI 工作台 CLI（阶段 1b 最小入口）")
    # 全局选项放在最前，所有子命令继承（写在所有子命令之前，如 `ws --home X run ppt`）。
    parser.add_argument("--home", help="工作目录根（默认 workspace/runtime；也可用 WORKSTATION_HOME）")
    parser.add_argument("--ppt-agent", help="PPTAgent 仓库根（也可用 WORKSTATION_PPT_AGENT / config）")
    parser.add_argument("--knowledge-root", help="知识库仓库根（也可用 WORKSTATION_KNOWLEDGE_ROOT / config）")

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="检查本机环境能否真正跑起来")

    p_run = sub.add_parser("run", help="执行一个技能")
    run_sub = p_run.add_subparsers(dest="run_skill", required=True)
    p_ppt = run_sub.add_parser("ppt", help="渲染 .pptx（PPTAgent 桥接）")
    p_ppt.add_argument("--input", required=True, help="任务输入 JSON（含 brief/facts/deck_spec 等）")
    p_ppt.add_argument("--pages", type=int, help="覆盖 requested_pages")
    p_ppt.add_argument("--accept-padding", action="store_true", help="显式接受框架稿（默认拒绝超量页数）")
    p_ppt.add_argument("--require-approval", action="store_true", help="要求 fs:primary 审批（默认阻塞）")
    p_ppt.add_argument("--dry-run", action="store_true", help="只组装请求不执行")
    p_ppt.add_argument("--priority", default="interactive", choices=["interactive", "batch", "low"])
    p_ppt.add_argument("--idempotency", help="幂等键，重复提交返回同一 Run")
    p_ppt.add_argument("--context", default="default", help="场景分区（红线#5：记忆不合并；检索按 context 收窄）")

    p_kb = run_sub.add_parser("knowledge", help="检索知识库 Vault（knowledge-bridge/1）")
    p_kb.add_argument("--input", required=True, help="任务输入 JSON（含 vault/query 等）")
    p_kb.add_argument("--vault", help="覆盖 vault 绝对路径")
    p_kb.add_argument("--query", help="覆盖 query")
    p_kb.add_argument("--limit", type=int, help="覆盖 limit（默认 8）")
    p_kb.add_argument("--require-approval", action="store_true", help="（只读检索无审批，保留兼容）")
    p_kb.add_argument("--dry-run", action="store_true", help="只组装请求不执行")
    p_kb.add_argument("--priority", default="interactive", choices=["interactive", "batch", "low"])
    p_kb.add_argument("--idempotency", help="幂等键，重复提交返回同一 Run")
    p_kb.add_argument("--context", default="default", help="场景分区（interview 等）")

    p_runs = sub.add_parser("runs", help="查看历史 Run")
    runs_sub = p_runs.add_subparsers(dest="runs_cmd", required=True)
    p_list = runs_sub.add_parser("list", help="列出历史 Run")
    p_list.add_argument("--status", help="按状态过滤")
    p_list.add_argument("--limit", type=int, default=20)
    p_show = runs_sub.add_parser("show", help="查看单个 Run 详情")
    p_show.add_argument("run_id")

    p_ret = sub.add_parser("retrieve", help="按场景隔离的跨源检索（阶段4 门面）")
    p_ret.add_argument("--query", required=True, help="检索 Query")
    p_ret.add_argument("--context", default="default",
                       help="场景分区：thesis / interview / default（默认不跨域）")
    p_ret.add_argument("--limit", type=int, default=10)
    p_ret.add_argument("--cross-context", action="store_true",
                       help="跨场景检索（显式 opt-in，红线#5：永不默认）")
    p_ret.add_argument("--knowledge-vault", help="知识库 Vault 绝对路径（默认 <knowledge-root>/knowledge 或 knowledge-root）")
    p_ret.add_argument("--markdown-folder", help="Markdown 目录（thesis 源）")
    p_ret.add_argument("--pdf-folder", help="PDF 目录（default 源）")

    p_serve = sub.add_parser("serve", help="启动本地工作台视图（仅监听 loopback）")
    p_serve.add_argument("--host", default="127.0.0.1", help="绑定地址（仅允许 loopback；默认 127.0.0.1）")
    p_serve.add_argument("--port", type=int, default=8787, help="端口（默认 8787）")
    p_serve.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    p_serve.add_argument("--knowledge-vault", help="知识库 Vault 绝对路径")
    p_serve.add_argument("--markdown-folder", help="Markdown 目录（thesis 源）")
    p_serve.add_argument("--pdf-folder", help="PDF 目录（default 源）")

    p_pdf = sub.add_parser("pdf", help="本地 PDF 解析工具（表格感知 + 双栏重排）")
    p_pdf.add_argument("rest", nargs=argparse.REMAINDER,
                       help="传给 workstation.tools.pdf 的参数（extract / chunks）")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "doctor":
        return cmd_doctor(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "runs":
        return cmd_runs(args)
    if args.cmd == "retrieve":
        return cmd_retrieve(args)
    if args.cmd == "serve":
        return cmd_serve(args)
    if args.cmd == "pdf":
        return pdf_cli.main(args.rest)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
