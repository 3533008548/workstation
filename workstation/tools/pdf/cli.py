"""PDF 工具 CLI：``python -m workstation.tools.pdf extract <file>``。

纯本地、不出网、不调模型。把论文转成 Markdown 文本或可检索片段。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .extract import chunkify, extract_document, extract_text


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="workstation.tools.pdf", description="本地 PDF 解析（表格感知 + 双栏重排）")
    sub = p.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("extract", help="解析 PDF 为 Markdown 文本或结构化 JSON")
    ex.add_argument("pdf", help="PDF 文件路径")
    ex.add_argument("--pages", type=int, default=15, help="最多读取页数（默认 15）")
    ex.add_argument("--max-chars", type=int, default=None, help="Markdown 输出最大字符数")
    ex.add_argument("--no-tables", action="store_true", help="关闭表格感知（确定性回退）")
    ex.add_argument("--json", action="store_true", help="输出结构化 JSON 而非 Markdown")
    ex.add_argument("--out", help="写出到文件（默认 stdout）")

    ch = sub.add_parser("chunks", help="把 PDF 切成可检索片段（JSON 数组）")
    ch.add_argument("pdf", help="PDF 文件路径")
    ch.add_argument("--pages", type=int, default=15, help="最多读取页数")
    ch.add_argument("--max-chars", type=int, default=900, help="每段最大字符数")
    ch.add_argument("--out", help="写出到文件（默认 stdout）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    path = Path(args.pdf)
    if not path.exists():
        print(f"找不到文件：{path}", file=sys.stderr)
        return 2
    try:
        if args.cmd == "extract":
            if args.json:
                doc = extract_document(path, extract_tables=not args.no_tables, max_pages=args.pages)
                text = json.dumps(doc, ensure_ascii=False, indent=2)
            else:
                text = extract_text(
                    path,
                    extract_tables=not args.no_tables,
                    max_pages=args.pages,
                    max_chars=args.max_chars,
                )
        else:  # chunks
            doc = extract_document(path, max_pages=args.pages)
            text = json.dumps(chunkify(doc, max_chars=args.max_chars), ensure_ascii=False, indent=2)
    except ImportError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 解析失败: {exc}", file=sys.stderr)
        return 1

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已写出: {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
