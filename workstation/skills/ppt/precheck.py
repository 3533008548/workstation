"""页数预检 —— 在付费调用之前先算"这些材料值几页"。

PPTAgent 的 P0 缺陷 #1 是一句话需求被扩写成 30 页填充内容。原始实现里
没有任何一处在调用模型**之前**问过"信息量够不够"，所以 29 次模型调用之后
才发现产出没有价值。

这里的预检是同一道门禁的底座侧实现，作用有两个：

1. **省一次子进程和一批模型调用**——不划算的请求在最便宜的地方被拒；
2. **给调用方一个可执行的答复**——不是"不行"，而是"你只有 4 条事实，
   支持约 1 页，可以补材料 / 降到 1 页 / 显式接受框架稿"。

⚠️ 权威门禁仍在 PPTAgent 侧（`precheckPages`）：预检只负责早退，不负责
终审。两侧公式必须一致，`tests/test_ppt_precheck.py` 与 PPTAgent 的
`tests/planner.test.ts` 用同一组数字向量对钉，公式改了会有一侧红灯。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from workstation_contracts import FactSet

__all__ = [
    "DEFAULT_FACTS_PER_PAGE",
    "MAX_RECOMMENDED_PAGES",
    "DeckFeasibility",
    "PageBudgetVerdict",
    "assess_deck_feasibility",
]

DEFAULT_FACTS_PER_PAGE = 3.0
MAX_RECOMMENDED_PAGES = 60
MIN_DECK_PAGES = 3


class PageBudgetVerdict(str, Enum):
    OK = "ok"
    """请求页数在信息量支持范围内。"""

    ACCEPTED_PADDING = "accepted_padding"
    """请求超出信息量，但调用方显式接受了框架稿。"""

    EXCEEDS_EVIDENCE = "exceeds_evidence"
    """请求超出信息量且未显式接受。拦下。"""

    OVER_BUDGET = "over_budget"
    """请求超过硬上限 max_pages。任何情况下都不放行。"""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    """一条可引用事实都没有，且没有接受框架稿。"""


@dataclass(frozen=True)
class DeckFeasibility:
    verdict: PageBudgetVerdict
    requested_pages: int | None
    recommended_pages: int
    effective_pages: int
    total_facts: int
    citable_facts: int
    facts_per_page: float
    density: float
    reasons: list[str] = field(default_factory=list)
    options: list[str] = field(default_factory=list)

    @property
    def is_blocking(self) -> bool:
        return self.verdict in (
            PageBudgetVerdict.EXCEEDS_EVIDENCE,
            PageBudgetVerdict.OVER_BUDGET,
            PageBudgetVerdict.INSUFFICIENT_EVIDENCE,
        )

    def describe(self) -> str:
        head = f"{self.verdict.value}: requested={self.requested_pages} recommended={self.recommended_pages}"
        return "; ".join([head, *self.reasons])


def assess_deck_feasibility(
    fact_set: FactSet,
    *,
    requested_pages: int | None = None,
    facts_per_page: float = DEFAULT_FACTS_PER_PAGE,
    max_pages: int | None = None,
    accept_padding: bool = False,
) -> DeckFeasibility:
    """判断这个页数请求值不值得跑。

    推荐页数直接复用 ``FactSet.recommends_page_count``（契约里已有的那个
    方法），不再写第二份公式——底座内部只有一处密度算法。
    """
    if facts_per_page <= 0:
        raise ValueError("facts_per_page must be positive")

    total_facts = len(fact_set.facts)
    citable = len(fact_set.citable)
    recommended = fact_set.recommends_page_count(facts_per_page)
    density = fact_set.density()

    def build(
        verdict: PageBudgetVerdict,
        effective: int,
        reasons: list[str],
        options: list[str],
    ) -> DeckFeasibility:
        return DeckFeasibility(
            verdict=verdict,
            requested_pages=requested_pages,
            recommended_pages=recommended,
            effective_pages=effective,
            total_facts=total_facts,
            citable_facts=citable,
            facts_per_page=facts_per_page,
            density=density,
            reasons=reasons,
            options=options,
        )

    fallback_pages = max(MIN_DECK_PAGES, requested_pages or MIN_DECK_PAGES)

    if recommended == 0 and not accept_padding:
        return build(
            PageBudgetVerdict.INSUFFICIENT_EVIDENCE,
            fallback_pages,
            [
                f"no citable fact ({total_facts} provided, "
                f"{citable} at confidence high/medium)"
            ],
            [
                "provide Fact objects whose source_ids are registered",
                "raise confidence to high/medium",
                "set accept_padding=True to deliberately produce a framework draft",
            ],
        )

    if requested_pages is not None and max_pages is not None and requested_pages > max_pages:
        return build(
            PageBudgetVerdict.OVER_BUDGET,
            max_pages,
            [f"requested {requested_pages} pages exceeds max_pages={max_pages}"],
            [f"lower requested_pages to <= {max_pages}"],
        )

    if requested_pages is not None and recommended > 0 and requested_pages > recommended:
        reason = (
            f"requested {requested_pages} pages but {citable} citable facts "
            f"support about {recommended} at {facts_per_page:g} facts/page"
        )
        if accept_padding:
            return build(
                PageBudgetVerdict.ACCEPTED_PADDING,
                requested_pages,
                [reason],
                [],
            )
        return build(
            PageBudgetVerdict.EXCEEDS_EVIDENCE,
            recommended,
            [reason],
            [
                "supply more facts with source_ids",
                f"lower requested_pages to <= {recommended}",
                "set accept_padding=True to deliberately produce a framework draft",
            ],
        )

    effective = max(MIN_DECK_PAGES, requested_pages or recommended or MIN_DECK_PAGES)
    reasons: list[str] = []
    if requested_pages is None and recommended > 0 and effective != recommended:
        reasons.append(
            f"evidence supports {recommended} pages; using {effective} to satisfy the "
            f"minimum {MIN_DECK_PAGES}-page structure"
        )

    return build(PageBudgetVerdict.OK, effective, reasons, [])
