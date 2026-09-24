"""PDF 目录检索源（词法）。服务于 ``default`` 场景，填补知识库的 PDF 缺口。

用 ``workstation.tools.pdf`` 把目录下的 PDF 切成片段，再词法召回。这等于在
工作台层把"知识库不能解析 PDF"这个缺口补上了 —— 纯本地、不出网。
"""

from __future__ import annotations

from pathlib import Path

from ....tools.pdf import chunkify, extract_document
from ..source import Hit, RetrievalSource
from ._lexical import Chunk, lexical_rank


class PdfFolderSource:
    """对某个文件夹下的 ``*.pdf`` 做本地解析 + 词法检索。"""

    def __init__(self, folder: str | Path, *, name: str = "pdf", contexts: list[str] | None = None):
        self.folder = Path(folder)
        self.name = name
        self.contexts = contexts or ["default"]
        self._chunks: list[Chunk] | None = None

    def _index(self) -> list[Chunk]:
        if self._chunks is not None:
            return self._chunks
        chunks: list[Chunk] = []
        if not self.folder.exists():
            self._chunks = chunks
            return chunks
        for pdf in sorted(self.folder.rglob("*.pdf")):
            rel = pdf.relative_to(self.folder).as_posix()
            try:
                doc = extract_document(pdf, max_pages=20)
            except Exception:  # pragma: no cover - defensive
                continue
            for idx, piece in enumerate(chunkify(doc, max_chars=900)):
                text = piece["text"]
                if not text:
                    continue
                chunks.append(
                    Chunk(
                        source=self.name,
                        doc_id=f"{rel}#p{piece['page']}-{idx}",
                        title=piece.get("source_file") or pdf.stem,
                        text=text,
                        page=piece.get("page"),
                        section=piece.get("section") or "",
                        uri=rel,
                    )
                )
        self._chunks = chunks
        return chunks

    def search(self, query: str, limit: int) -> list[Hit]:
        return lexical_rank(self._index(), query, limit)

    def __repr__(self) -> str:  # pragma: no cover
        return f"PdfFolderSource({self.folder}, contexts={self.contexts})"
