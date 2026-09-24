"""阶段 5 端到端：把工作台当**一个入口**用。

不再是分别调 CLI 子命令，而是起一个只监听 loopback 的本地服务，用 HTTP 走完：
查健康 → 按场景检索 → 提交 Run → 事件回放。前端视图（``/``）调的就是这套接口。

    python examples/stage5_server.py                 # 跑一遍自检然后退出
    python examples/stage5_server.py --serve         # 保持运行，浏览器开 http://127.0.0.1:8787/
    python examples/stage5_server.py --real-knowledge  # 检索也真调知识库（需要 Node）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from workstation.core.server import build_workbench, create_server, serve  # noqa: E402

DEFAULT_KB = Path("D:/develop/agent for obsidian")


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(base: str, path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _show_retrieve(base: str, query: str, context: str, cross: bool = False) -> None:
    r = _post(base, "/api/retrieve", {
        "query": query, "context": context, "limit": 5, "cross_context": cross,
    })
    print(f"\n检索 context={r['context']}  cross={r['cross_context']}  merged={r['merged']}")
    print(f"  query : {r['query']}")
    srcs = ", ".join(f"{k}({len(v)})" for k, v in r["sources"].items()) or "(none)"
    print(f"  命中源 : {srcs}")
    for i, h in enumerate(r["hits"][:5], 1):
        tail = f"  p{h['page']}" if h.get("page") else ""
        print(f"  [{i}] ({h['source']}) {h['title']}  score={h['score']}{tail}")
        print(f"       {' '.join(h['text'].split())[:110]}")


def self_check(base: str, vault: str, real_knowledge: bool) -> None:
    h = _get(base, "/api/health")
    print(f"health   : {h['status']}  v{h['version']}  contracts {h['contract_version']}")
    print(f"home     : {h['home']}")
    print(f"场景     : {', '.join(h['contexts'])}")
    print(f"技能     : {', '.join(h['skills'])}")
    print(f"检索源   : {', '.join(h['sources'])}")

    _show_retrieve(base, "知识蒸馏", "thesis")
    _show_retrieve(base, "rate limiter", "default")

    if real_knowledge:
        _show_retrieve(base, "langgraph 和 langchain 区别", "interview")
    else:
        print("\n（跳过 interview 检索：真调知识库需要 Node。加 --real-knowledge 开启）")

    # 跨域是显式 opt-in：同一 query，开与不开命中源不同。
    _show_retrieve(base, "知识蒸馏", "interview", cross=True)

    run = _post(base, "/api/runs", {
        "skill": "knowledge-search",
        "context": "interview",
        "inputs": {"vault": vault, "query": "langchain"},
        "options": {"dry_run": True},
    })
    print(f"\n提交 Run : {run['run_id']}  status={run['status']}  context={run['context']}")
    print(f"  steps  : {len(run['steps'])}  cost=¥{run['usage']['cost_cny']:.4f}")
    print(f"  outputs: executed={run['outputs'].get('executed')}  "
          f"cost_accounting={run['outputs'].get('cost_accounting')}")

    events = urllib.request.urlopen(
        f"{base}/api/runs/{run['run_id']}/events", timeout=30
    ).read().decode("utf-8")
    print(f"  事件回放: {events.count('data:')} 条 SSE 事件")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="阶段 5：本地工作台统一入口")
    ap.add_argument("--home", default=str(REPO / "runtime"))
    ap.add_argument("--knowledge-root", default=os.environ.get("WORKSTATION_KNOWLEDGE_ROOT") or str(DEFAULT_KB))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--serve", action="store_true", help="自检后保持运行（供浏览器访问）")
    ap.add_argument("--open", action="store_true", help="配合 --serve 自动打开浏览器")
    ap.add_argument("--real-knowledge", action="store_true", help="检索也真调知识库（需要 Node 与 esbuild）")
    args = ap.parse_args(argv)

    kb = Path(args.knowledge_root)
    vault = os.environ.get("WORKSTATION_KNOWLEDGE_VAULT") or str(
        (kb / "knowledge") if (kb / "knowledge").exists() else kb
    )

    wb = build_workbench(
        home=args.home,
        ppt_agent=Path(os.environ.get("WORKSTATION_PPT_AGENT", "D:/develop/project/PPTagent")),
        knowledge_root=kb,
        vault=vault,
        markdown_folder=str(REPO / "examples" / "fixtures" / "markdown"),
        pdf_folder=str(REPO / "examples" / "fixtures" / "pdfs"),
    )

    httpd = create_server(wb, host=args.host, port=args.port)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"工作台已启动：{base}/  （仅监听 loopback）\n")

    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        self_check(base, vault, args.real_knowledge)
        if args.serve:
            print(f"\n保持运行：{base}/   —— Ctrl-C 停止")
            if args.open:
                import webbrowser

                webbrowser.open(base + "/")
            t.join()
    except KeyboardInterrupt:
        print("\n已停止。")
    except Exception as exc:  # noqa: BLE001 - 自检失败要给出可操作的提示
        print(f"\n自检失败：{exc}", file=sys.stderr)
        return 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        wb.store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
