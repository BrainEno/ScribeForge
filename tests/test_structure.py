"""Tests for the page-role / printed-page / chapter model (issue #87).

The fixtures are OCR results with realistic geometry rather than real page
images: what is under test is the reading of evidence, not the raster.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.ocr import (
    BlockType,
    BoundingBox,
    OCRBlock,
    OCRLine,
    PageOCRResult,
)
from scribeforge.domain.structure import (
    BookStructure,
    Chapter,
    NumberingScheme,
    PageLabel,
    PageRole,
    PageStructure,
    assign_sequences,
    format_roman,
    looks_like_heading,
    parse_cjk_numeral,
    parse_page_label,
    parse_roman,
)
from scribeforge.pipeline.structure import (
    build_book_structure,
    detect_chapters,
    detect_page_label,
    persist_book_structure,
)
from scribeforge.storage.sqlite import SQLiteStore


def _line(text: str, *, y: float, height: float = 0.02) -> OCRLine:
    return OCRLine(text, BoundingBox(0.1, y, 0.8, height), ())


def _page(
    lines: list[OCRLine],
    *,
    page_index: int = 0,
    blocks: tuple[OCRBlock, ...] = (),
) -> PageOCRResult:
    return PageOCRResult(
        page_index=page_index,
        engine="fixture",
        engine_version="1.0",
        lines=tuple(lines),
        blocks=blocks,
    )


# ------------------------------------------------------------------ numeral parsing


@pytest.mark.parametrize(
    ("text", "value"),
    [("iv", 4), ("IX", 9), ("xii", 12), ("XLVIII", 48), ("i", 1)],
)
def test_roman_numerals_parse(text: str, value: int) -> None:
    assert parse_roman(text) == value


@pytest.mark.parametrize("text", ["iiii", "vv", "", "ivx", "abc"])
def test_non_canonical_roman_is_rejected(text: str) -> None:
    assert parse_roman(text) is None


def test_mix_is_technically_roman_but_never_a_page_number() -> None:
    """``mix`` = 1009 is canonical roman, yet no book numbers a page that way.

    The numeral parser stays faithful to the notation; the *page label* parser
    applies the plausibility rule (roman folios <= 100, uniform case).
    """

    assert parse_roman("mix") == 1009
    assert not parse_page_label("mix").is_numbered


def test_format_roman_round_trips() -> None:
    for value in (1, 4, 9, 14, 40, 90, 400, 1994):
        assert parse_roman(format_roman(value)) == value


@pytest.mark.parametrize(
    ("text", "value"),
    [("一", 1), ("十", 10), ("十二", 12), ("二十三", 23), ("一百零五", 105), ("三百", 300)],
)
def test_cjk_numerals_parse(text: str, value: int) -> None:
    assert parse_cjk_numeral(text) == value


@pytest.mark.parametrize(
    ("raw", "scheme", "sequence"),
    [
        ("12", NumberingScheme.ARABIC, 12),
        (" 12 ", NumberingScheme.ARABIC, 12),
        (" 12. ", NumberingScheme.ARABIC, 12),
        ("· 7 ·", NumberingScheme.ARABIC, 7),
        ("iv", NumberingScheme.ROMAN_LOWER, 4),
        ("XIV", NumberingScheme.ROMAN_UPPER, 14),
        ("二十三", NumberingScheme.CJK, 23),
    ],
)
def test_parse_page_label_accepts_realistic_margins(
    raw: str, scheme: NumberingScheme, sequence: int
) -> None:
    label = parse_page_label(raw)

    assert label.scheme is scheme
    assert label.sequence == sequence
    assert label.is_numbered


@pytest.mark.parametrize(
    "raw",
    ["译林出版社", "", "   ", "1923年", "第一章 在星期二的早晨", "12abc", "mix", "IVXii"],
)
def test_parse_page_label_refuses_to_guess(raw: str) -> None:
    """A running head or a year is not a folio, and guessing corrupts citations."""

    label = parse_page_label(raw)

    assert not label.is_numbered
    assert label.printed_label == ""


def test_a_single_roman_margin_line_is_dropped_as_a_running_head() -> None:
    """This book's own title is "V.", which parses as roman 5.

    One isolated detection carries no evidence of a numbering sequence, so the
    sequence resolver discards it while keeping real front-matter runs.
    """

    lone = assign_sequences([(40, PageLabel("V", NumberingScheme.ROMAN_UPPER, 5))])
    front_matter = assign_sequences(
        [
            (0, PageLabel("I", NumberingScheme.ROMAN_UPPER, 1)),
            (1, PageLabel("II", NumberingScheme.ROMAN_UPPER, 2)),
        ]
    )

    assert dict(lone) == {}
    assert [label.sequence for label in front_matter.values()] == [1, 2]


def test_page_label_rejects_sequence_zero() -> None:
    with pytest.raises(ValueError, match="start at 1"):
        PageLabel("0", NumberingScheme.ARABIC, 0)


def test_numbered_label_requires_printed_text() -> None:
    with pytest.raises(ValueError, match="printed text"):
        PageLabel("", NumberingScheme.ARABIC, 1)


# ------------------------------------------------------------------- sequences


def test_sequences_restart_when_numbering_goes_backwards() -> None:
    """This book restarts numbering per section; the runs must stay distinct."""

    labels = assign_sequences(
        [
            (0, PageLabel("1", NumberingScheme.ARABIC, 1)),
            (1, PageLabel("2", NumberingScheme.ARABIC, 2)),
            (2, PageLabel("1", NumberingScheme.ARABIC, 1)),
            (3, PageLabel("2", NumberingScheme.ARABIC, 2)),
        ]
    )

    first_run = labels[0].sequence_ordinal
    second_run = labels[2].sequence_ordinal
    assert first_run is not None and second_run is not None
    assert first_run // 1_000_000 == 1
    assert second_run // 1_000_000 == 2


def test_sequences_reject_a_large_forward_jump_as_a_misread() -> None:
    labels = assign_sequences(
        [
            (0, PageLabel("10", NumberingScheme.ARABIC, 10)),
            (1, PageLabel("11", NumberingScheme.ARABIC, 11)),
            (2, PageLabel("97", NumberingScheme.ARABIC, 97)),
            (3, PageLabel("12", NumberingScheme.ARABIC, 12)),
        ],
        max_gap=3,
    )

    assert 2 not in labels
    assert labels[3].sequence == 12


def test_sequences_tolerate_a_small_gap_from_a_missing_page() -> None:
    labels = assign_sequences(
        [
            (0, PageLabel("10", NumberingScheme.ARABIC, 10)),
            (1, PageLabel("13", NumberingScheme.ARABIC, 13)),
        ]
    )

    assert labels[1].sequence == 13


def test_unnumbered_candidates_are_skipped() -> None:
    labels = assign_sequences([(0, PageLabel("", NumberingScheme.NONE, None))])

    assert dict(labels) == {}


# ---------------------------------------------------------------- page detection


def _numbered_page(number: str, page_index: int) -> PageOCRResult:
    footer_y = 0.96
    lines = [
        _line("正文第一行内容在此", y=0.2),
        _line(number, y=footer_y, height=0.015),
    ]
    block = OCRBlock(
        block_type=BlockType.FOOTER,
        box=BoundingBox(0.4, footer_y, 0.2, 0.015),
        line_start=1,
        line_end=2,
    )
    return _page(lines, page_index=page_index, blocks=(block,))


def test_footer_block_supplies_the_printed_page_number() -> None:
    label = detect_page_label(_numbered_page("23", 0))

    assert label.is_numbered
    assert label.sequence == 23
    assert label.scheme is NumberingScheme.ARABIC


def test_body_numbers_are_not_mistaken_for_folios() -> None:
    """A year or a chapter number in the body must not become the page label."""

    page = _page(
        [
            _line("1923", y=0.2),
            _line("第十二章", y=0.3),
            _line("这是正文。", y=0.4),
        ]
    )

    assert not detect_page_label(page).is_numbered


def test_margin_band_is_used_when_the_engine_reports_no_blocks() -> None:
    """v1 workers report no blocks at all; geometry must still find the folio."""

    page = _page([_line("正文", y=0.4), _line("7", y=0.965, height=0.012)])

    assert detect_page_label(page).sequence == 7


def test_a_textual_running_head_is_not_a_page_number() -> None:
    head = _page([_line("托马斯·品钦", y=0.02, height=0.015), _line("正文", y=0.4)])

    assert not detect_page_label(head).is_numbered


# ------------------------------------------------------------------- chapters


def test_declared_title_block_becomes_a_chapter() -> None:
    title_line = _line("第一章", y=0.3, height=0.05)
    body = _line("正文内容。", y=0.4, height=0.02)
    block = OCRBlock(
        block_type=BlockType.TITLE,
        box=BoundingBox(0.3, 0.3, 0.4, 0.05),
        line_start=0,
        line_end=1,
    )
    page = _page([title_line, body], blocks=(block,))

    chapters = detect_chapters(page, 5)

    assert chapters == ((1, "第一章"),)


def test_larger_typeset_heading_is_detected_without_a_title_block() -> None:
    """Engines that do not classify blocks still give usable geometry."""

    heading = _line("第二章 在星期二的早晨", y=0.3, height=0.045)
    body_lines = [_line("正文内容。", y=0.4 + index * 0.03, height=0.02) for index in range(5)]
    page = _page([heading, *body_lines])

    chapters = detect_chapters(page, 12)

    assert [title for _, title in chapters] == ["第二章 在星期二的早晨"]


def test_a_chapter_pattern_at_body_size_is_not_a_heading() -> None:
    """Mentioning 第一章 inside prose must not split the book."""

    line = _line("第一章里写道，", y=0.3, height=0.02)
    body = [_line("正文内容。", y=0.35 + index * 0.03, height=0.02) for index in range(5)]
    page = _page([line, *body])

    assert detect_chapters(page, 3) == ()


def test_heading_patterns() -> None:
    assert looks_like_heading("第十二章")
    assert looks_like_heading("前言")
    assert looks_like_heading("一、缘起")
    assert not looks_like_heading("他说，")
    assert not looks_like_heading("这是一段很长的正文，不应该被当作标题来处理，因为太长了")


# ----------------------------------------------------------------- whole book


def _store_with_run(tmp_path: Path, pages: list[PageOCRResult]) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", "v.pdf", "hash")
    for result in pages:
        store.add_page(
            project_id, result.page_index, f"page-{result.page_index:03d}.png", 100, 150
        )
        store.record_ocr(project_id, result)
    return store, project_id


def test_build_book_structure_resolves_numbers_and_chapters_across_pages(
    tmp_path: Path,
) -> None:
    title = _line("第一章", y=0.3, height=0.05)
    body = _line("正文内容。", y=0.4, height=0.02)
    title_block = OCRBlock(
        block_type=BlockType.TITLE,
        box=BoundingBox(0.3, 0.3, 0.4, 0.05),
        line_start=0,
        line_end=1,
    )
    first = _page([title, body], page_index=0, blocks=(title_block,))
    pages = [first] + [_numbered_page(str(index), index) for index in range(1, 4)]
    store, project_id = _store_with_run(tmp_path, pages)

    structure = build_book_structure(store, project_id, engine="fixture")

    assert structure.chapters
    assert structure.chapters[0].title == "第一章"
    assert structure.chapters[0].start_page_index == 0
    # Page 0 has a footer-less body, so no printed label; pages 1..3 do.
    assert [page.label.sequence for page in structure.pages[1:]] == [1, 2, 3]
    assert structure.chapter_at(0) == structure.chapters[0]
    assert structure.page_range(structure.chapters[0]) == (0, 3)


def test_structure_round_trips_through_the_store(tmp_path: Path) -> None:
    store, project_id = _store_with_run(
        tmp_path, [_numbered_page(str(index), index) for index in range(1, 4)]
    )
    structure = build_book_structure(store, project_id, engine="fixture")

    report = persist_book_structure(store, project_id, structure)
    loaded = store.load_book_structure(project_id)

    assert report.chapters == len(structure.chapters)
    assert len(report.labelled_pages) == len(structure.pages)
    assert loaded.pages == structure.pages
    assert loaded.chapters == structure.chapters


def test_persisting_structure_replaces_rather_than_accumulates(tmp_path: Path) -> None:
    store, project_id = _store_with_run(
        tmp_path, [_numbered_page("1", 0), _numbered_page("2", 1)]
    )
    structure = build_book_structure(store, project_id, engine="fixture")
    persist_book_structure(store, project_id, structure)

    persist_book_structure(
        store,
        project_id,
        BookStructure(
            pages=(
                PageStructure(0, PageRole.COVER, PageLabel("", NumberingScheme.NONE, None)),
                PageStructure(1, PageRole.BLANK, PageLabel("", NumberingScheme.NONE, None)),
            ),
            chapters=(),
        ),
    )

    loaded = store.load_book_structure(project_id)
    assert [page.role for page in loaded.pages] == [PageRole.COVER, PageRole.BLANK]
    assert loaded.chapters == ()


def test_book_structure_rejects_out_of_order_pages() -> None:
    with pytest.raises(ValueError, match="ordered by page index"):
        BookStructure(
            pages=(PageStructure(1), PageStructure(0)),
            chapters=(),
        )


def test_chapter_at_returns_the_deepest_containing_chapter() -> None:
    structure = BookStructure(
        pages=tuple(PageStructure(index) for index in range(10)),
        chapters=(
            Chapter(0, 1, "第一部", 0),
            Chapter(1, 2, "第一章", 2),
            Chapter(2, 2, "第二章", 6),
        ),
    )

    assert structure.chapter_at(3) == structure.chapters[1]
    assert structure.chapter_at(7) == structure.chapters[2]
    assert structure.page_range(structure.chapters[1]) == (2, 5)
    assert structure.page_range(structure.chapters[2]) == (6, 9)


def test_display_label_falls_back_to_the_physical_page() -> None:
    numbered = PageStructure(4, PageRole.BODY, PageLabel("12", NumberingScheme.ARABIC, 12))
    unnumbered = PageStructure(4)

    assert numbered.display_label == "12"
    assert unnumbered.display_label == "5"


def test_blank_pages_are_reported() -> None:
    structure = BookStructure(
        pages=(
            PageStructure(0, PageRole.COVER),
            PageStructure(1, PageRole.BLANK),
            PageStructure(2, PageRole.BLANK),
        ),
        chapters=(),
    )

    assert structure.blank_pages() == (1, 2)
