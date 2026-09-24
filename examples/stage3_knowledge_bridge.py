"""阶段 3 端到端示例 —— 知识库只读检索桥接。

演示一条完整调用链：

    查询 + Vault 路径
        → 适配器映射成 BridgeRequest
        → 起真实 Node 子进程（knowledge-bridge/1，esbuild 打包 core/）
        → 读 response.json
        → KB 的 SourceRef 映射成底座 SourceRef（derive_source_id 去重）
        → Run / Artifact 落盘

不依赖任何 API key、不出网、不写 Vault（只读）。失败也会留下 Run。

运行：
    python examples/stage3_knowledge_bridge.py
    python examples/stage3_knowledge_bridge.py --query "langgraph 和 langchain 区别" --limit 3
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from workstation import __version__
from workstation_contracts import RunStatus, TaskOptions, TaskRequest
from workstation.core.runtime import SkillRuntime, open_run_store
from workstation.skills.knowledge import (
    KNOWLEDGE_SKILL_NAME,
    KnowledgeSkill,
    KnowledgeSkillConfig,
    SubprocessRunner,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_KB = Path("D:/develop/agent for obsidian")
DEFAULT_VAULT = DEFAULT_KB / "knowledge" / "agentlearning-知识环迁移"


def main() -> int:
    ap = argparse.ArgumentParser(prog="stage3", description="知识库检索桥接端到端示例")
    ap.add_argument("--home", default=str(REPO / "runtime"))
    ap.add_argument("--knowledge-root", default=os.environ.get("WORKSTATION_KNOWLEDGE_ROOT", str(DEFAULT_KB)))
    ap.add_argument("--vault", default=str(DEFAULT_VAULT))
    ap.add_argument("--query", default="RAG 中的 RRF 算法 k 有什么影响")
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()

    kb = Path(args.knowledge_root)
    vault = Path(args.vault)

    if not (kb / "src" / "bridge.ts").exists():
        print(f"[skip] 知识库桥接入口不存在：{kb / 'src' / 'bridge.ts'}")
        return 0
    if not vault.is_dir():
        print(f"[skip] vault 不存在：{vault}")
        return 0

    home = Path(args.home)
    inputs = {"vault": str(vault), "query": args.query, "limit": args.limit}
    request = TaskRequest(skill=KNOWLEDGE_SKILL_NAME, inputs=inputs, options=TaskOptions())

    config = KnowledgeSkillConfig(workspace_home=home, knowledge_root=kb)
    store = open_run_store(home)
    runtime = SkillRuntime(store, {KNOWLEDGE_SKILL_NAME: KnowledgeSkill(config, SubprocessRunner())})
    run = runtime.submit(request)

    print(f"workstation {__version__}")
    print(f"run_id : {run.run_id}")
    print(f"status : {run.status.value}")
    if run.error:
        print(f"error  : {run.error}")
    print(
        f"indexed: {run.outputs.get('indexed_files')} files / "
        f"{run.outputs.get('chunk_count')} chunks"
    )
    print(f"hits   : {run.outputs.get('hit_count')}")
    for hit in run.outputs.get("hits", [])[: args.limit]:
        print(f"  [{hit['score']:.0f}] {hit['title']}  {hit['uri']}")
        print(f"       {hit['excerpt'][:80]}")
    print(f"source_ids: {run.source_ids}")
    print(f"cost   : {run.outputs.get('cost_accounting')}（纯本地只读检索，无 token/费用）")
    print(f"checkpoint: {run.checkpoint_ref}（进程崩溃可原位重放）")
    return 0 if run.status is RunStatus.SUCCEEDED else 1


if __name__ == "__main__":
    raise SystemExit(main())
