"""知识库检索技能适配器（knowledge-loop-desktop / Node 一次性子进程）。

    from workstation.skills.knowledge import KnowledgeSkill, KnowledgeSkillConfig, SubprocessRunner

    skill = KnowledgeSkill(
        KnowledgeSkillConfig(workspace_home=Path("runtime"), knowledge_root=Path("D:/develop/agent for obsidian")),
        SubprocessRunner(),
    )
    run = skill.execute(TaskRequest(skill="knowledge-search", inputs={...}))

契约面是 ``knowledge-bridge/1``：请求与响应都在 ``contract.py`` 里以
``extra="forbid"`` 重新声明，与知识库侧 ``src/bridge-schema.ts`` 的严格校验互为校验。
阶段 3 仅只读检索，因此权限面最小（subprocess + fs:derived），不写 Vault、
不调模型、不出网。
"""

from __future__ import annotations

from .contract import (
    BRIDGE_API_VERSION,
    BridgeChunk,
    BridgeRequest,
    BridgeResponse,
    BridgeResult,
    BridgeSourceRef,
)
from .precheck import SearchFeasibility, SearchVerdict, assess_search
from .runner import Runner, RunnerError, RunnerResult, RunnerTimeout, SubprocessRunner
from .skill import (
    KNOWLEDGE_SKILL_NAME,
    KnowledgeSkill,
    KnowledgeSkillConfig,
    KnowledgeTaskInput,
    knowledge_skill_manifest,
)

__all__ = [
    "BRIDGE_API_VERSION",
    "BridgeChunk",
    "BridgeRequest",
    "BridgeResponse",
    "BridgeResult",
    "BridgeSourceRef",
    "KNOWLEDGE_SKILL_NAME",
    "KnowledgeSkill",
    "KnowledgeSkillConfig",
    "KnowledgeTaskInput",
    "Runner",
    "RunnerError",
    "RunnerResult",
    "RunnerTimeout",
    "SearchFeasibility",
    "SearchVerdict",
    "SubprocessRunner",
    "assess_search",
    "knowledge_skill_manifest",
]
