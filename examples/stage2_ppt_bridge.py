"""Stage-2 smoke: the PPT skill, from Fact to .pptx.

    python examples/stage2_ppt_bridge.py

Two halves:

1. **Gate demo (no Node, no network).** A ``FakeRunner`` drives the page-budget
   precheck, so you can see exactly which requests are refused and what the
   caller is told instead.
2. **Real bridge (needs Node).** Renders ``spec/demo.deck.json`` through the
   actual one-shot subprocess and prints the verification the adapter got back.

Point 2 is the interesting one: nothing about the .pptx is simulated. If Node
or PPTAgent is missing, that half reports why and the gate demo still runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from ppt_fakes import SOURCE_A, SOURCE_B, FakeRunner, bridge_response  # noqa: E402
from workstation.skills.ppt import (  # noqa: E402
    PPT_SKILL_NAME,
    PageBudgetVerdict,
    PptSkill,
    PptSkillConfig,
    SubprocessRunner,
    assess_deck_feasibility,
)
from workstation_contracts import (  # noqa: E402
    Confidence,
    Fact,
    FactSet,
    RunStatus,
    TaskOptions,
    TaskRequest,
)

PPT_AGENT_ROOT = Path("D:/develop/project/PPTagent")


def rule(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 66 - len(title)))


def make_facts(count: int, *, confidence: Confidence = Confidence.HIGH) -> list[Fact]:
    return [
        Fact(
            text=f"第 {index} 条已登记事实的正文内容",
            source_ids=[SOURCE_A if index % 2 else SOURCE_B],
            confidence=confidence,
        )
        for index in range(1, count + 1)
    ]


def brief() -> dict:
    return {
        "title": "Q3 业务复盘",
        "audience": "管理层",
        "goal": "确认下一步优先行动",
        "required_points": ["增长质量", "成本结构", "下一步动作"],
    }


# --------------------------------------------------------------------- 门禁


def gate_demo() -> None:
    rule("1. 页数门禁：信息量决定能出几页")

    scenarios = [
        ("12 条事实要 4 页", make_facts(12), 4, None, False),
        ("4 条事实要 30 页", make_facts(4), 30, None, False),
        ("4 条事实要 30 页 + 接受框架稿", make_facts(4), 30, None, True),
        ("40 条事实要 9 页，硬上限 6", make_facts(40), 9, 6, True),
        ("0 条可引用事实（全是 low）", make_facts(5, confidence=Confidence.LOW), 6, None, False),
    ]

    for label, facts, requested, max_pages, accept in scenarios:
        verdict = assess_deck_feasibility(
            FactSet(facts=facts),
            requested_pages=requested,
            max_pages=max_pages,
            accept_padding=accept,
        )
        mark = "拦下" if verdict.is_blocking else "放行"
        print(f"\n  {label}")
        print(
            f"    → {mark}｜{verdict.verdict.value}｜可引用 {verdict.citable_facts} 条"
            f"｜推荐 {verdict.recommended_pages} 页｜实际 {verdict.effective_pages} 页"
        )
        for reason in verdict.reasons:
            print(f"      reason: {reason}")
        for option in verdict.options:
            print(f"      option: {option}")


def fake_run_demo(config: PptSkillConfig) -> None:
    rule("2. 被拒时仍然留下可追究的 Run（不起子进程）")

    runner = FakeRunner()
    skill = PptSkill(config, runner)
    run = skill.execute(
        TaskRequest(
            skill=PPT_SKILL_NAME,
            inputs={
                "brief": brief(),
                "facts": [fact.model_dump() for fact in make_facts(4)],
                "requested_pages": 30,
            },
        )
    )

    print(f"  status        : {run.status.value}")
    print(f"  error         : {run.error}")
    print(f"  subprocess    : {len(runner.calls)} 次（预期 0）")
    print(f"  source_ids    : {run.source_ids}")
    print(f"  steps         : {[(step.name, step.status.value) for step in run.steps]}")


# ----------------------------------------------------------------- 真实桥接


def real_bridge_demo(config: PptSkillConfig) -> bool:
    rule("3. 真实桥接：Python → npx tsx → PPTAgent → .pptx")
    print(f"  PPTAgent: {config.ppt_agent_root}")

    deck_spec_path = config.ppt_agent_root / "spec" / "demo.deck.json"
    if not deck_spec_path.exists():
        print(f"  跳过：找不到 {deck_spec_path}")
        return False

    deck_spec = json.loads(deck_spec_path.read_text(encoding="utf-8"))
    # 注意：本进程（工作台）不调模型，但 PPTAgent 的 bridge 命令在渲染前
    # 仍会走 compress 路由做事实摘要 —— 因此 WORKSTATION_DEEPSEEK_API_KEY
    # 必须设置，否则桥接会在模型调用处失败（见 run ppt 的 BRIDGE_FAILED）。
    facts = [
        Fact(
            fact_id=item["id"],
            text=item["text"],
            source_ids=item["sourceIds"],
            confidence=Confidence(item["confidence"]),
        )
        for item in deck_spec["facts"]
    ]

    skill = PptSkill(config, SubprocessRunner())
    run = skill.execute(
        TaskRequest(
            skill=PPT_SKILL_NAME,
            inputs={
                "brief": brief(),
                "facts": [fact.model_dump() for fact in facts],
                "mode": "render",
                "deck_spec": deck_spec,
                "requested_pages": 10,
                "accept_padding": True,
            },
            options=TaskOptions(priority="interactive"),
        )
    )

    print(f"  status        : {run.status.value}")
    if run.status is not RunStatus.SUCCEEDED:
        print(f"  error         : {run.error}")
        return False

    print(f"  slides        : {run.outputs['slide_count']}")
    print(f"  pptx          : {run.artifacts[0].uri}")
    print(f"  bytes         : {Path(run.artifacts[0].uri).stat().st_size:,}")
    print(f"  fact → page   : {run.outputs['used_fact_ids']}")
    print(f"  source_ids    : {run.source_ids}")
    print(f"  cost          : {run.outputs['cost_accounting']}（{run.outputs['unmetered']}）")
    print(f"  steps         : {[(step.name, step.status.value, step.usage.wall_clock_s) for step in run.steps]}")
    return True


def main() -> None:
    config = PptSkillConfig(workspace_home=REPO / "runtime", ppt_agent_root=PPT_AGENT_ROOT)

    gate_demo()
    fake_run_demo(config)
    try:
        real_bridge_demo(config)
    except Exception as exc:  # 真实桥接依赖本机 Node，失败要说清原因而不是静默跳过
        print(f"  真实桥接失败：{type(exc).__name__}: {exc}")

    print("\n产物在 runtime/ 下（已 gitignore）：primary/decks/<run_id>/ 与 derived/runs/<run_id>/")


if __name__ == "__main__":
    main()
