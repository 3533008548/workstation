"""PPT 技能适配器（PPTAgent / Node 一次性子进程）。

    from workstation.skills.ppt import PptSkill, PptSkillConfig, SubprocessRunner

    skill = PptSkill(
        PptSkillConfig(workspace_home=Path("runtime"), ppt_agent_root=Path("D:/develop/project/PPTagent")),
        SubprocessRunner(),
    )
    run = skill.execute(TaskRequest(skill="ppt-deck", inputs={...}))

契约面是 ``ppt-bridge/1``：请求与响应都在 ``contract.py`` 里以
``extra="forbid"`` 重新声明，与 PPTAgent 的 zod 定义互为校验。
"""

from __future__ import annotations

from .contract import (
    BRIDGE_API_VERSION,
    BridgeRequest,
    BridgeResponse,
    BridgeFact,
    BridgeBrief,
    BridgePagePlan,
)
from .precheck import (
    DEFAULT_FACTS_PER_PAGE,
    DeckFeasibility,
    PageBudgetVerdict,
    assess_deck_feasibility,
)
from .runner import Runner, RunnerError, RunnerResult, RunnerTimeout, SubprocessRunner
from .skill import PPT_SKILL_NAME, PptSkill, PptSkillConfig, PptTaskInput, ppt_skill_manifest

__all__ = [
    "BRIDGE_API_VERSION",
    "DEFAULT_FACTS_PER_PAGE",
    "DeckFeasibility",
    "PPT_SKILL_NAME",
    "PageBudgetVerdict",
    "PptSkill",
    "PptSkillConfig",
    "PptTaskInput",
    "Runner",
    "RunnerError",
    "RunnerResult",
    "RunnerTimeout",
    "SubprocessRunner",
    "assess_deck_feasibility",
    "ppt_skill_manifest",
    "BridgeBrief",
    "BridgeFact",
    "BridgePagePlan",
    "BridgeRequest",
    "BridgeResponse",
]
