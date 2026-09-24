"""页数预检的判据与边界。

这组数字向量必须与 PPTAgent 的 `tests/planner.test.ts`
（`recommended page count mirrors the workbench fact-density formula`）
完全一致：两侧算出的"这些材料值几页"不能有分歧，否则底座会放行一个
子进程必然会拒的请求（或反之）。
"""

from __future__ import annotations

import pytest

from workstation.skills.ppt.precheck import (
    PageBudgetVerdict,
    assess_deck_feasibility,
)
from workstation_contracts import Confidence, Fact, FactSet, SourceType, SourceRef

SOURCE = "src_" + "c" * 24


def facts(count: int, *, confidence: Confidence = Confidence.HIGH) -> list[Fact]:
    return [
        Fact(
            text=f"第 {index} 条已登记事实的正文内容",
            source_ids=[SOURCE],
            confidence=confidence,
        )
        for index in range(1, count + 1)
    ]


def fact_set(count: int, **kwargs: object) -> FactSet:
    return FactSet(facts=facts(count, **kwargs))  # type: ignore[arg-type]


def source_ref() -> SourceRef:
    return SourceRef.mint(
        source_type=SourceType.PAPER,
        uri="paper://doi/10.1000/xyz",
        title="示例论文",
        producer="research.paper",
    )


def test_recommended_pages_matches_the_cross_language_vectors() -> None:
    # 与 PPTAgent tests/planner.test.ts 同一组向量。
    assert fact_set(0).recommends_page_count(3.0) == 0
    assert fact_set(4).recommends_page_count(3.0) == 1
    assert fact_set(12).recommends_page_count(3.0) == 4
    assert fact_set(500).recommends_page_count(3.0) == 60


def test_requested_page_count_inside_the_supported_range_passes() -> None:
    verdict = assess_deck_feasibility(fact_set(12), requested_pages=4)

    assert verdict.verdict is PageBudgetVerdict.OK
    assert verdict.is_blocking is False
    assert verdict.effective_pages == 4
    assert verdict.recommended_pages == 4
    assert verdict.citable_facts == 12


def test_one_sentence_thirty_pages_is_blocked_before_anything_runs() -> None:
    verdict = assess_deck_feasibility(fact_set(4), requested_pages=30)

    assert verdict.verdict is PageBudgetVerdict.EXCEEDS_EVIDENCE
    assert verdict.is_blocking is True
    assert verdict.recommended_pages == 1
    assert verdict.effective_pages == 1
    assert "support about 1" in verdict.reasons[0]
    assert any("accept_padding" in option for option in verdict.options)


def test_accept_padding_downgrades_the_block_but_still_reports_it() -> None:
    verdict = assess_deck_feasibility(fact_set(4), requested_pages=30, accept_padding=True)

    assert verdict.verdict is PageBudgetVerdict.ACCEPTED_PADDING
    assert verdict.is_blocking is False
    assert verdict.effective_pages == 30


def test_no_citable_fact_at_all_is_blocked() -> None:
    verdict = assess_deck_feasibility(fact_set(0), requested_pages=6)

    assert verdict.verdict is PageBudgetVerdict.INSUFFICIENT_EVIDENCE
    assert verdict.recommended_pages == 0
    assert "no citable fact" in verdict.reasons[0]


def test_low_and_unknown_confidence_facts_are_not_citable() -> None:
    low = assess_deck_feasibility(fact_set(5, confidence=Confidence.LOW), requested_pages=4)
    unknown = assess_deck_feasibility(fact_set(5, confidence=Confidence.UNKNOWN), requested_pages=4)

    assert low.verdict is PageBudgetVerdict.INSUFFICIENT_EVIDENCE
    assert unknown.verdict is PageBudgetVerdict.INSUFFICIENT_EVIDENCE
    # 事实总量仍然是报出来的，只是可引用数为 0。
    assert low.total_facts == 5
    assert low.citable_facts == 0


def test_max_pages_is_a_hard_ceiling_even_with_padding_accepted() -> None:
    verdict = assess_deck_feasibility(
        fact_set(40), requested_pages=9, max_pages=6, accept_padding=True
    )

    assert verdict.verdict is PageBudgetVerdict.OVER_BUDGET
    assert verdict.is_blocking is True


def test_unspecified_page_count_falls_back_to_the_evidence_and_respects_the_minimum() -> None:
    verdict = assess_deck_feasibility(fact_set(12))

    assert verdict.verdict is PageBudgetVerdict.OK
    assert verdict.recommended_pages == 4
    assert verdict.effective_pages == 4
    assert verdict.reasons == []


def test_a_tiny_evidence_base_still_produces_a_legal_three_page_deck() -> None:
    verdict = assess_deck_feasibility(fact_set(2))

    assert verdict.recommended_pages == 1
    assert verdict.effective_pages == 3
    assert "minimum 3-page structure" in verdict.reasons[0]


def test_density_is_reported_for_the_caller_to_judge() -> None:
    verdict = assess_deck_feasibility(fact_set(6), requested_pages=2)

    assert verdict.density > 0
    assert verdict.total_facts == 6


def test_zero_facts_per_page_is_rejected() -> None:
    with pytest.raises(ValueError):
        assess_deck_feasibility(fact_set(6), facts_per_page=0)


def test_the_adapter_only_needs_registered_source_ids() -> None:
    # 事实必须带 source_ids —— 契约本身不允许无来源事实存在。
    with pytest.raises(ValueError):
        Fact(text="无来源的断言", source_ids=[])

    assert source_ref().source_id.startswith("src_")
