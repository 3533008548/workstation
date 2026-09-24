"""阶段 6 端到端：把科研助手接进工作台（毕设域）。

科研助手是**容器化的长驻 HTTP 服务**（docker-compose 把它发布在 `127.0.0.1:7860`）。
所以本示例有两种跑法：

    python examples/stage6_research.py                 # 打真实服务（需先 docker compose up -d）
    python examples/stage6_research.py --demo          # 起内置桩服务，完整演示提交 → 恢复
    python examples/stage6_research.py --query "..."   # 指定研究问题

真实链路：health → 建会话 → POST /runs(202) → 拿句柄当 checkpoint_ref → resume 轮询终态。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workstation.core.runtime import SkillRuntime, open_run_store  # noqa: E402
from workstation.skills.research import (  # noqa: E402
    RESEARCH_SKILL_NAME,
    ResearchAgentClient,
    ResearchServiceConfig,
    ResearchSkill,
    ResearchSkillConfig,
)
from workstation_contracts import RunStatus, TaskRequest  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:7860"
DEFAULT_QUERY = "知识蒸馏在边缘设备推理上的最新进展"


# ------------------------------------------------------------------ 内置桩


class _Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        return

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def do_GET(self):
        if self.path == "/api/v1/health":
            self._send(200, {"status": "ok"})
        elif self.path == "/api/v1/workspace/papers":
            self._send(200, [{"paper_id": "p1", "title": "Knowledge Distillation on Edge", "chunks": 12}])
        elif self.path.startswith("/api/v1/runs/"):
            self._send(200, {"run_id": self.path.rsplit("/", 1)[-1], "status": _STUB_STATUS[0]})
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self):
        body = self._read()
        if self.path == "/api/v1/sessions":
            self._send(201, {"thread_id": "sess-demo", "title": body.get("title", "")})
        elif self.path == "/api/v1/runs":
            self._send(202, {
                "run_id": "research-demo", "kind": "research",
                "session_id": body.get("session_id"), "status": "queued",
                "stream_url": "/api/v1/runs/research-demo/events",
            })
        else:
            self._send(404, {"detail": "not found"})


_STUB_STATUS = ["queued"]


def start_stub() -> tuple[str, ThreadingHTTPServer]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", httpd


# ------------------------------------------------------------------ 演示


def run_flow(base_url: str, query: str, home: Path) -> int:
    service = ResearchServiceConfig(base_url=base_url, timeout_s=15)
    client = ResearchAgentClient(service)

    print(f"服务      : {base_url}")
    print(f"health    : {'就绪' if client.health() else '不可用'}")

    try:
        papers = client.list_papers()
        print(f"论文库    : {len(papers)} 篇" + (f"  例：{papers[0].title}" if papers else ""))
    except Exception as exc:  # noqa: BLE001
        print(f"论文库    : 读取失败（{exc}）")

    skill = ResearchSkill(ResearchSkillConfig(workspace_home=home, service=service))
    store = open_run_store(home)
    runtime = SkillRuntime(store, {RESEARCH_SKILL_NAME: skill})
    try:
        run = runtime.submit(TaskRequest(
            skill=RESEARCH_SKILL_NAME,
            inputs={"query": query, "scope": "both"},
            context="thesis",
        ))
        print(f"\n提交 Run  : {run.run_id}  status={run.status.value}  context={run.context}")
        if run.status is not RunStatus.SUCCEEDED:
            print(f"  失败：{run.error}")
            return 1
        print(f"  session  : {run.outputs.get('session_id')}")
        print(f"  agent_run: {run.outputs.get('agent_run_id')}  status={run.outputs.get('agent_status')}")
        print(f"  checkpoint_ref（对端事件流）: {run.checkpoint_ref}")
        print(f"  记账      : {run.outputs.get('cost_accounting')}  cost=¥{run.usage.cost_cny:.4f}")

        _STUB_STATUS[0] = "completed"
        resumed = skill.resume(run)
        print(f"\nresume    : 对端终态 {resumed.outputs.get('agent_status')} → "
              f"工作台 Run {resumed.status.value}")
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="阶段 6：科研助手接入（毕设域）")
    ap.add_argument("--url", default=os.environ.get("WORKSTATION_RESEARCH_URL") or DEFAULT_URL)
    ap.add_argument("--query", default=DEFAULT_QUERY)
    ap.add_argument("--home", default=str(REPO / "runtime"))
    ap.add_argument("--demo", action="store_true",
                    help="起内置桩服务演示完整流程（无需 Docker）")
    args = ap.parse_args(argv)

    home = Path(args.home)
    home.mkdir(parents=True, exist_ok=True)

    if args.demo:
        print("（--demo：使用内置桩服务，不代表真实科研助手）")
        url, httpd = start_stub()
        try:
            return run_flow(url, args.query, home)
        finally:
            httpd.shutdown()
            httpd.server_close()
    else:
        if not ResearchAgentClient(ResearchServiceConfig(base_url=args.url)).health():
            print(f"科研助手不可用：{args.url}", file=sys.stderr)
            print("先启动容器：cd D:/develop/academic/research_agent && docker compose up -d",
                  file=sys.stderr)
            print("或用 --demo 起内置桩服务先看流程。", file=sys.stderr)
            return 1
        return run_flow(args.url, args.query, home)


if __name__ == "__main__":
    raise SystemExit(main())
