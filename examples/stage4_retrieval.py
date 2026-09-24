"""阶段 4 端到端：按场景隔离的跨源检索。

演示三件事：
  1. 知识库源（interview）只服务面试场景；
  2. Markdown 目录源（thesis）只服务毕设场景；
  3. 跨场景检索是显式 opt-in（--cross-context），绝不默认。

PDF 源（default）现场造一篇 PDF，证明"知识库不能解析 PDF"的缺口已在
工作台层补齐。纯本地、不出网。

    python examples/stage4_retrieval.py
    python examples/stage4_retrieval.py --query "langgraph" --context interview
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from workstation.core.retrieval import RetrievalService
from workstation.core.retrieval.sources import (
    KnowledgeRetrievalSource,
    MarkdownFolderSource,
    PdfFolderSource,
)

REPO = Path(__file__).resolve().parents[1]
KB_DEFAULT = Path("D:/develop/agent for obsidian")


def _default_vault(kb: Path) -> str:
    cand = kb / "knowledge"
    return str(cand) if cand.exists() else str(kb)


def _make_pdf_fixture(folder: Path) -> None:
    """造一篇关于"面试常见系统设计题"的 PDF，喂给 default 场景的 PDF 源。"""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "System Design Interview", fontsize=14)
    page.insert_text((72, 100), "Design a rate limiter: token bucket vs sliding window.", fontsize=11)
    page.insert_text((72, 130), "Design a URL shortener: base62 encoding and collision handling.", fontsize=11)
    page.insert_text((72, 160), "Consistent hashing maps keys to nodes on a ring.", fontsize=11)
    out = folder / "system-design-interview.pdf"
    doc.save(str(out))
    doc.close()


def _print(result) -> None:
    print(f"\n=== query={result.query!r}  context={result.context}  "
          f"cross={result.cross_context}  merged={result.merged} ===")
    print(f"sources : {', '.join(result.per_source) or '(none)'}")
    print(f"hits    : {len(result.hits)}")
    for i, h in enumerate(result.hits[:6], 1):
        tail = f"  p{h.page}" if h.page else ""
        print(f"  [{i}] ({h.source}) {h.title}  score={h.score:.3f}{tail}")
        print(f"       {' '.join(str(h.text).split())[:120]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="阶段4 检索门面端到端")
    ap.add_argument("--query", help="自定义查询（覆盖默认三连演示）")
    ap.add_argument("--context", default="interview", choices=["thesis", "interview", "default"])
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--cross-context", action="store_true")
    ap.add_argument("--knowledge-root", default=str(KB_DEFAULT))
    args = ap.parse_args()

    kb = Path(os.environ.get("WORKSTATION_KNOWLEDGE_ROOT") or args.knowledge_root)
    vault = os.environ.get("WORKSTATION_KNOWLEDGE_VAULT") or _default_vault(kb)
    md = REPO / "examples" / "fixtures" / "markdown"

    pdf_dir = Path(tempfile.mkdtemp(prefix="stage4-pdf-"))
    _make_pdf_fixture(pdf_dir)

    service = RetrievalService()
    service.register(KnowledgeRetrievalSource(knowledge_root=kb, vault=vault))
    service.register(MarkdownFolderSource(md))
    service.register(PdfFolderSource(pdf_dir))

    if args.query:
        _print(service.retrieve(args.query, context=args.context, limit=args.limit, cross_context=args.cross_context))
        return 0

    # 默认三连演示：场景隔离是硬约束，不是开关。
    _print(service.retrieve("langgraph 和 langchain 区别", context="interview", limit=args.limit))
    _print(service.retrieve("知识蒸馏 学生网络", context="thesis", limit=args.limit))
    _print(service.retrieve("rate limiter 限流 设计", context="default", limit=args.limit))
    _print(service.retrieve("注意力机制", context="interview", limit=args.limit, cross_context=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
