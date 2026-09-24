"""Reciprocal Rank Fusion（RRF）跨源排序合并。

RRF 只依赖各源返回的**排序**，不依赖各源分数的量纲 —— 这正是它能把
"知识库词法检索" 与 "Markdown 目录检索" 这种异构打分直接融合的原因。
"""

from __future__ import annotations

from .source import Hit


def rrf_merge(ranked_lists: list[list[Hit]], k: int = 60) -> list[Hit]:
    """合并多份已排序命中为一份融合排序。

    ``k`` 为平滑常数（Cormack et al. 2009 建议 60）。返回按融合分降序的列表，
    同分时保持各源出现的稳定顺序。
    """
    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    merged: dict[str, Hit] = {}

    for li, ranked in enumerate(ranked_lists):
        for rank, hit in enumerate(ranked):
            key = hit.ref
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in merged:
                merged[key] = hit
                order[key] = (li, rank)

    items = sorted(scores.items(), key=lambda kv: (-kv[1], order[kv[0]]))
    result: list[Hit] = []
    for key, score in items:
        hit = merged[key]
        hit = Hit(**{**hit.__dict__, "score": round(score, 6)})
        result.append(hit)
    return result
