"""阶段 4 PDF 工具层测试 —— 用 PyMuPDF 现场造一篇论文，避免提交二进制 fixture。"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from workstation.tools.pdf import chunkify, extract_document, extract_text


def _make_paper(path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Introduction", fontsize=14)
    page.insert_text((72, 100), "We propose a novel method for low-power inference on edge devices.", fontsize=11)
    page.insert_text((72, 130), "Method", fontsize=14)
    page.insert_text(
        (72, 158),
        "Our approach uses knowledge distillation with a student network of 3 layers.",
        fontsize=11,
    )
    page.insert_text((72, 188), "Table 1 shows the accuracy across baselines.", fontsize=11)
    page.insert_text((72, 220), "| Model | Acc |", fontsize=10)
    page.insert_text((72, 236), "| --- | --- |", fontsize=10)
    page.insert_text((72, 252), "| Ours | 0.91 |", fontsize=10)
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def paper(tmp_path: Path) -> Path:
    pdf = tmp_path / "demo_paper.pdf"
    _make_paper(pdf)
    return pdf


def test_extract_text_reads_sections(paper: Path) -> None:
    md = extract_text(paper, max_pages=5)
    assert "Introduction" in md
    assert "Method" in md
    assert "knowledge distillation" in md
    # 章节被标注为 Markdown 二级标题
    assert "## Introduction" in md


def test_chunkify_labels_section_correctly(paper: Path) -> None:
    doc = extract_document(paper, max_pages=5)
    chunks = chunkify(doc)
    assert chunks, "应有片段"
    # 引言正文应标在 Introduction 下，而非被下一个标题吞掉（阶段4 修复的 bug）
    intro = [c for c in chunks if c["section"] == "Introduction"]
    assert intro, "应有 Introduction 片段"
    assert any("low-power inference" in c["text"] for c in intro)
    # 没有任何片段错标成 Method
    mislabeled = [c for c in chunks if c["section"] == "Method" and "low-power inference" in c["text"]]
    assert not mislabeled


def test_extract_document_shape(paper: Path) -> None:
    doc = extract_document(paper, max_pages=5)
    assert doc["source_file"] == "demo_paper.pdf"
    assert doc["processed_pages"] == 1
    assert doc["pages"]
    assert doc["pages"][0]["elements"]


def test_missing_file_raises(paper: Path) -> None:
    import pytest as _pytest

    with _pytest.raises(FileNotFoundError):
        extract_text(paper.parent / "nope.pdf")
