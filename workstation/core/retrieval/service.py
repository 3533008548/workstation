"""RetrievalService —— 按 context 收窄的检索编排。

设计要点（红线 #5：记忆不合并，但检索可跨源）：
  * 每个源声明 ``contexts``（如 knowledge→["interview"]，markdown→["thesis"]）。
  * ``retrieve(query, context=X)`` 只查声明了 X 的源。
  * ``cross_context=True`` 才查全部源 —— 跨场景是显式 opt-in，永不默认。
  * 结果用 RRF 融合成一份排序，但各源原始命中也保留（便于溯源/调试）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .rrf import rrf_merge
from .source import Hit, RetrievalSource


@dataclass
class RetrievalResult:
    query: str
    context: str
    cross_context: bool
    hits: list[Hit] = field(default_factory=list)
    per_source: dict[str, list[Hit]] = field(default_factory=dict)
    merged: bool = False

    def top(self, n: int | None = None) -> list[Hit]:
        return self.hits[:n] if n is not None else self.hits


class RetrievalService:
    def __init__(self) -> None:
        self._sources: dict[str, RetrievalSource] = {}

    def register(self, source: RetrievalSource) -> "RetrievalService":
        if source.name in self._sources:
            raise ValueError(f"检索源重名：{source.name}")
        self._sources[source.name] = source
        return self

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    def sources_for(self, context: str, *, cross_context: bool) -> list[RetrievalSource]:
        if cross_context:
            return list(self._sources.values())
        return [s for s in self._sources.values() if context in s.contexts]

    def retrieve(
        self,
        query: str,
        *,
        context: str = "default",
        limit: int = 10,
        cross_context: bool = False,
    ) -> RetrievalResult:
        chosen = self.sources_for(context, cross_context=cross_context)
        per_source: dict[str, list[Hit]] = {}
        ranked_lists: list[list[Hit]] = []
        for src in chosen:
            hits = src.search(query, limit)
            per_source[src.name] = hits
            ranked_lists.append(hits)

        merged_hits = rrf_merge(ranked_lists) if len(ranked_lists) > 1 else (ranked_lists[0] if ranked_lists else [])
        return RetrievalResult(
            query=query,
            context=context,
            cross_context=cross_context,
            hits=merged_hits[:limit],
            per_source=per_source,
            merged=len(ranked_lists) > 1,
        )
