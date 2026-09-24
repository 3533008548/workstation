"""Markdown 目录检索源（词法）。服务于 ``thesis`` 场景。

把本地 Markdown 目录按标题切成片段，查询时做词法召回。这是"Markdown 入 Chroma"
的本地轻量形态；将来换嵌入向量时，只改本源的 ``search``，RRF 融合层不动。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..source import Hit, RetrievalSource
from ._lexical import Chunk, lexical_rank

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


class MarkdownFolderSource:
    """对某个文件夹下的 ``*.md`` 做词法检索。"""

    def __init__(self, folder: str | Path, *, name: str = "markdown", contexts: list[str] | None = None):
        self.folder = Path(folder)
        self.name = name
        self.contexts = contexts or ["thesis"]
        self._chunks: list[Chunk] | None = None

    def _index(self) -> list[Chunk]:
        if self._chunks is not None:
            return self._chunks
        chunks: list[Chunk] = []
        if not self.folder.exists():
            self._chunks = chunks
            return chunks
        for md in sorted(self.folder.rglob("*.md")):
            rel = md.relative_to(self.folder).as_posix()
            text = md.read_text(encoding="utf-8", errors="replace")
            # 按标题切片；无标题则整篇一段。
            parts = _HEADING_RE.split(text)
            # parts: [pre, h1, h1title, body, h2, h2title, body, ...]
            title = md.stem
            if len(parts) <= 1:
                body = parts[0].strip()
                if body:
                    chunks.append(Chunk(source=self.name, doc_id=rel, title=title, text=body, uri=rel))
                continue
            # parts[0] 是首个标题前的引言
            pre = parts[0].strip()
            if pre:
                chunks.append(Chunk(source=self.name, doc_id=rel, title=title, text=pre, uri=rel))
            for i in range(1, len(parts), 3):
                heading = parts[i + 1].strip() if i + 1 < len(parts) else ""
                body = parts[i + 2].strip() if i + 2 < len(parts) else ""
                if body:
                    chunks.append(
                        Chunk(
                            source=self.name,
                            doc_id=f"{rel}#{heading}" if heading else rel,
                            title=heading or title,
                            text=body,
                            section=heading,
                            uri=rel,
                        )
                    )
        self._chunks = chunks
        return chunks

    def search(self, query: str, limit: int) -> list[Hit]:
        return lexical_rank(self._index(), query, limit)

    def __repr__(self) -> str:  # pragma: no cover
        return f"MarkdownFolderSource({self.folder}, contexts={self.contexts})"
