"""Reading order and vertical paragraph splicing as an explicit layer."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from scribeforge.adapters.docx import build_docx
from scribeforge.adapters.epub import build_epub
from scribeforge.adapters.export_check import check_docx, check_epub
from scribeforge.domain.content import VerifiedBook, VerifiedPage
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageDirection
from scribeforge.domain.reading_order import (
    order_indexes,
    reading_order_key,
    splice_paragraphs,
)

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _line(text: str, x: float, y: float, width: float = 0.06, height: float = 0.3) -> OCRLine:
    return OCRLine(text, BoundingBox(x, y, width, height))


def test_horizontal_order_is_top_to_bottom_then_left_to_right() -> None:
    lines = (
        _line("右下", x=0.7, y=0.5),
        _line("左上", x=0.1, y=0.1),
        _line("左下", x=0.1, y=0.5),
    )

    assert order_indexes(lines, PageDirection.HORIZONTAL_LR) == (1, 2, 0)


def test_horizontal_right_to_left_order_reverses_columns() -> None:
    lines = (
        _line("左", x=0.1, y=0.1),
        _line("右", x=0.7, y=0.1),
    )

    assert order_indexes(lines, PageDirection.HORIZONTAL_RL) == (1, 0)


def test_vertical_right_to_left_starts_at_the_rightmost_column() -> None:
    """The bug this layer exists for: sorting vertical columns by X ascending.

    A vertical page's first column is the rightmost one. Getting this wrong
    reverses the whole book while every line still looks plausible on its own.
    """

    lines = (
        _line("第三列", x=0.20, y=0.1),
        _line("第一列", x=0.70, y=0.1),
        _line("第二列", x=0.45, y=0.1),
    )

    assert order_indexes(lines, PageDirection.VERTICAL_RL) == (1, 2, 0)


def test_vertical_left_to_right_starts_at_the_leftmost_column() -> None:
    lines = (
        _line("第二列", x=0.45, y=0.1),
        _line("第一列", x=0.20, y=0.1),
    )

    assert order_indexes(lines, PageDirection.VERTICAL_LR) == (1, 0)


def test_vertical_order_breaks_column_ties_top_to_bottom() -> None:
    lines = (
        _line("下", x=0.70, y=0.6),
        _line("上", x=0.70, y=0.1),
    )

    assert reading_order_key(lines[1], PageDirection.VERTICAL_RL) < reading_order_key(
        lines[0], PageDirection.VERTICAL_RL
    )
    assert order_indexes(lines, PageDirection.VERTICAL_RL) == (1, 0)


def test_horizontal_splicing_leaves_lines_untouched() -> None:
    """Horizontal lines are visual lines; joining them needs evidence we lack."""

    lines = ("甲乙", "丙丁。", "戊己")

    assert splice_paragraphs(lines, PageDirection.HORIZONTAL_LR) == lines


def test_vertical_columns_join_without_a_separator() -> None:
    """A vertical column is a paragraph fragment, not a paragraph.

    Joining must not insert a space: a Latin word or number split across columns
    would be corrupted, and Chinese needs no separator at all.
    """

    lines = ("甲乙丙", "丁戊己", "庚辛壬。")

    assert splice_paragraphs(lines, PageDirection.VERTICAL_RL) == ("甲乙丙丁戊己庚辛壬。",)


def test_vertical_sentence_end_starts_a_new_paragraph() -> None:
    lines = ("甲乙。", "丙丁", "戊己。", "庚辛")

    assert splice_paragraphs(lines, PageDirection.VERTICAL_RL) == (
        "甲乙。",
        "丙丁戊己。",
        "庚辛",
    )


def test_vertical_blank_line_is_an_explicit_break() -> None:
    lines = ("甲乙", "", "丙丁")

    assert splice_paragraphs(lines, PageDirection.VERTICAL_RL) == ("甲乙", "丙丁")


def test_vertical_trailing_closing_quote_closes_a_paragraph() -> None:
    lines = ("他说：「好。", "」", "下一段。")

    assert splice_paragraphs(lines, PageDirection.VERTICAL_RL) == (
        "他说：「好。",
        "」",
        "下一段。",
    )


def test_verified_page_uses_its_direction_for_paragraphs() -> None:
    horizontal = VerifiedPage(0, ("甲", "乙"))
    vertical = VerifiedPage(0, ("甲", "乙"), direction=PageDirection.VERTICAL_RL)

    assert horizontal.paragraphs() == ("甲", "乙")
    assert vertical.paragraphs() == ("甲乙",)


def _book(direction: PageDirection) -> VerifiedBook:
    return VerifiedBook(
        "書名",
        (
            VerifiedPage(
                0,
                ("第一列文字", "第二列文字。", "新的段落"),
                printed_label="1",
                direction=direction,
            ),
        ),
    )


def test_epub_splices_vertical_columns_into_paragraphs() -> None:
    book = _book(PageDirection.VERTICAL_RL)
    data = build_epub(
        book,
        identifier="urn:test",
        language="zh-Hant",
        modified=datetime(2026, 1, 1, tzinfo=UTC),
    )

    check_epub(data, book)

    with zipfile.ZipFile(BytesIO(data)) as archive:
        page_name = next(name for name in archive.namelist() if "page-0" in name)
        text = archive.read(page_name).decode("utf-8")

    assert "<p>第一列文字第二列文字。</p>" in text
    assert "<p>新的段落</p>" in text
    # No separator was inserted between the joined columns.
    assert "第一列文字 第二列文字" not in text


def test_docx_splices_vertical_columns_into_paragraphs() -> None:
    book = _book(PageDirection.VERTICAL_RL)
    data = build_docx(
        book,
        identifier="urn:test",
        language="zh-Hant",
        modified=datetime(2026, 1, 1, tzinfo=UTC),
    )

    check_docx(data, book)

    with zipfile.ZipFile(BytesIO(data)) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = [
        "".join(token.text or "" for token in paragraph.iter(f"{{{_WORD_NS}}}t"))
        for paragraph in document.findall(f"{{{_WORD_NS}}}body/{{{_WORD_NS}}}p")
        if not list(paragraph.iter(f"{{{_WORD_NS}}}br"))
    ]

    assert paragraphs == ["第一列文字第二列文字。", "新的段落"]


def test_export_check_rejects_a_splice_that_swallows_a_line(monkeypatch) -> None:
    """Splicing must re-group lines, never lose one."""

    import pytest

    from scribeforge.adapters import export_check

    book = _book(PageDirection.VERTICAL_RL)
    broken = VerifiedPage(0, ("甲", "乙"), direction=PageDirection.VERTICAL_RL)

    monkeypatch.setattr(
        type(broken), "paragraphs", lambda self: ("甲",), raising=True
    )
    object.__setattr__(book, "pages", (broken,))

    with pytest.raises(export_check.ExportIntegrityError, match="lost or gained text"):
        export_check._require_no_text_lost(book)


def test_assembly_records_the_alignment_direction(tmp_path: Path) -> None:
    """The export splice rule must follow the direction the alignment used."""

    from scribeforge.domain.alignment import LineAlignment, PageAlignment
    from scribeforge.domain.ocr import PageOCRResult
    from scribeforge.domain.risk import PairRisk
    from scribeforge.pipeline.assembly import assemble_verified_content
    from scribeforge.storage.sqlite import SQLiteStore

    texts = ("第一列文字", "第二列文字。")
    lines = tuple(
        OCRLine(text, BoundingBox(0.7 - 0.2 * index, 0.1, 0.06, 0.8), ())
        for index, text in enumerate(texts)
    )
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "source.pdf", "hash")
    store.add_page(project_id, 0, "pages/0001.png", 1000, 2000)
    primary_id = store.record_ocr(
        project_id, PageOCRResult(0, "mineru", "1", lines)
    )
    secondary_id = store.record_ocr(
        project_id, PageOCRResult(0, "paddleocr", "1", lines)
    )
    alignment = PageAlignment(
        0,
        "mineru",
        "paddleocr",
        tuple(LineAlignment(index, index, 1.0, 0.0, ()) for index in range(len(texts))),
        direction=PageDirection.VERTICAL_RL,
    )
    risks = tuple(PairRisk(index, 0.0, ()) for index in range(len(texts)))
    store.record_verification(primary_id, secondary_id, alignment, risks, ())
    for index, text in enumerate(texts):
        store.append_decision(
            project_id=project_id,
            page_index=0,
            pair_index=index,
            selected_text=text,
            actor="automatic",
            evidence_refs=(),
        )

    book = assemble_verified_content(store, project_id)

    assert book.pages[0].direction is PageDirection.VERTICAL_RL
    assert book.pages[0].paragraphs() == ("第一列文字第二列文字。",)
