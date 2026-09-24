"""词法检索共享逻辑（Markdown 目录源 / PDF 目录源复用）。

这是"Markdown 入 Chroma"的本地轻量替代：阶段 4 先用词法排序把检索门面跑通，
嵌入向量（Chroma + bge-m3）作为同一源的 drop-in 升级，不影响 RRF 融合层。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..source import Hit

_TOKEN_RE = re.compile(r"[A-Za-z0-9一-鿿]+")


@dataclass
class Chunk:
    """一个可检索片段。``doc_id`` 在同源内须唯一。"""

    source: str
    doc_id: str
    title: str
    text: str
    page: int | None = None
    section: str = ""
    uri: str = ""


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def lexical_rank(chunks: list[Chunk], query: str, limit: int) -> list[Hit]:
    """按查询词在片段中的出现频次排序（词法召回，非语义）。"""
    q_tokens = [t for t in _tokens(query) if len(t) > 1]
    if not q_tokens:
        return []

    scored: list[tuple[int, Chunk]] = []
    for chunk in chunks:
        lower = chunk.text.lower()
        score = sum(lower.count(qt) for qt in q_tokens)
        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    hits: list[Hit] = []
    for score, chunk in scored[:limit]:
        hits.append(
            Hit(
                source=chunk.source,
                doc_id=chunk.doc_id,
                title=chunk.title,
                text=chunk.text,
                score=float(score),
                page=chunk.page,
                uri=chunk.uri,
                section=chunk.section,
            )
        )
    return hits
