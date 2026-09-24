"""阶段 4 检索源集合。

  * knowledge  → interview 场景（knowledge-bridge/1 只读检索）
  * markdown   → thesis   场景（本地 Markdown 目录，词法）
  * pdf        → default  场景（本地 PDF 解析，词法；填补知识库 PDF 缺口）

新源只需实现 ``RetrievalSource`` 协议，注册进 ``RetrievalService`` 即可，RRF
融合层不感知其实现。
"""

from __future__ import annotations

from .knowledge_source import KnowledgeRetrievalSource
from .markdown_source import MarkdownFolderSource
from .pdf_source import PdfFolderSource

__all__ = [
    "KnowledgeRetrievalSource",
    "MarkdownFolderSource",
    "PdfFolderSource",
]
