"""Tests for page-furniture detection (issue #89).

The dangerous failure is deleting real text, so most of these tests assert what
must *not* be removed.
"""

from __future__ import annotations

from pathlib import Path

from scribeforge.domain.furniture import (
    REASON_DECLARED_BLOCK,
    REASON_PAGE_NUMBER,
    REASON_REPEATED_IN_MARGIN,
    detect_page_furniture,
    furniture_line_indexes,
    repeated_margin_texts,
)
from scribeforge.domain.ocr import (
    BlockType,
    BoundingBox,
    OCRBlock,
    OCRLine,
    PageOCRResult,
)


def _line(text: str, *, y: float, height: float = 0.02) -> OCRLine:
    return OCRLine(text, BoundingBox(0.1, y, 0.8, height), ())


def _page(
    lines: list[OCRLine],
    *,
    page_index: int,
    blocks: tuple[OCRBlock, ...] = (),
) -> PageOCRResult:
    return PageOCRResult(
        page_index=page_index,
        engine="fixture",
        engine_version="1.0",
        lines=tuple(lines),
        blocks=blocks,
    )


def _running_head_book(pages: int = 4) -> dict[int, PageOCRResult]:
    """Pages that all carry the same running head and a folio footer."""

    result: dict[int, PageOCRResult] = {}
    for index in range(pages):
        result[index] = _page(
            [
                _line("托马斯·品钦", y=0.02, height=0.012),
                _line("正文第一行", y=0.2),
                _line("正文第二行", y=0.24),
                _line(str(index + 5), y=0.96, height=0.012),
            ],
            page_index=index,
        )
    return result


def test_repeated_running_head_and_folio_are_detected() -> None:
    detected = detect_page_furniture(_running_head_book())

    assert detected[0] == {0: REASON_REPEATED_IN_MARGIN, 3: REASON_PAGE_NUMBER}
    assert all(set(found) == {0, 3} for found in detected.values())


def test_body_lines_are_never_touched() -> None:
    detected = furniture_line_indexes(_running_head_book())

    for indexes in detected.values():
        assert 1 not in indexes
        assert 2 not in indexes


def test_a_margin_line_on_one_page_is_kept() -> None:
    """A chapter title printed at the top margin is content, not furniture."""

    pages = _running_head_book()
    pages[2] = _page(
        [
            _line("第一章 在星期二的早晨", y=0.03, height=0.05),
            _line("正文第一行", y=0.2),
            _line("正文第二行", y=0.24),
            _line("7", y=0.96, height=0.012),
        ],
        page_index=2,
    )

    detected = furniture_line_indexes(pages)

    assert 0 not in detected.get(2, frozenset())


def test_a_page_with_a_single_line_keeps_it() -> None:
    """Nothing to be furniture *relative to*: a lone line is content."""

    pages = {
        0: _page([_line("正文", y=0.02)], page_index=0),
        1: _page([_line("正文", y=0.02)], page_index=1),
        2: _page([_line("正文", y=0.02)], page_index=2),
    }

    assert furniture_line_indexes(pages) == {}


def test_engine_declared_footer_is_removed_even_if_unique() -> None:
    """The engine already decided this block is page furniture; trust it."""

    lines = [
        _line("正文第一行", y=0.2),
        _line("译林出版社", y=0.94, height=0.012),
    ]
    block = OCRBlock(
        block_type=BlockType.FOOTER,
        box=BoundingBox(0.1, 0.94, 0.8, 0.012),
        line_start=1,
        line_end=2,
    )
    pages = {0: _page(lines, page_index=0, blocks=(block,))}

    assert furniture_line_indexes(pages) == {0: frozenset({1})}
    assert detect_page_furniture(pages)[0][1] == REASON_DECLARED_BLOCK


def test_repetition_threshold_is_respected() -> None:
    """Two pages sharing a line can be a part-title spread, not a pattern."""

    pages = _running_head_book(pages=2)

    detected = furniture_line_indexes(pages)

    # The folio is still furniture (it is a bare number); the head is not.
    assert detected[0] == frozenset({3})


def test_a_running_head_that_looks_like_a_roman_numeral_is_still_removed() -> None:
    """This book's own title is "V.", which is also a legal roman numeral.

    It is excluded either way, but it is reported as a page number rather than as
    repeated text. The ambiguity is real and is recorded rather than hidden: the
    structure pass additionally refuses to treat a repeated identical numeral as a
    numbering sequence, so it never becomes a printed page label.
    """

    pages = {
        index: _page(
            [_line("V.", y=0.02, height=0.012), _line("正文", y=0.2)], page_index=index
        )
        for index in range(3)
    }

    detected = detect_page_furniture(pages)

    assert all(found[0] == REASON_PAGE_NUMBER for found in detected.values())


def test_variant_forms_of_a_running_head_count_as_the_same_text() -> None:
    """Two engines reading the head in different scripts must still match."""

    pages: dict[int, PageOCRResult] = {}
    for index, head in enumerate(["說", "说", "説", "說", "说"]):
        pages[index] = _page([_line(head, y=0.02, height=0.012), _line("正文", y=0.2)], page_index=index)

    detected = furniture_line_indexes(pages)

    assert all(0 in found for found in detected.values())


def test_repeated_margin_texts_reports_what_would_be_removed() -> None:
    pages = list(_running_head_book().values())

    repeated = repeated_margin_texts(pages)

    assert repeated["托马斯·品钦"] == 4
    # Folios differ per page, so they are not "repeated text".
    assert "5" not in repeated


def test_empty_pages_are_handled() -> None:
    pages = {0: _page([], page_index=0), 1: _page([], page_index=1)}

    assert furniture_line_indexes(pages) == {}


def test_head_and_folio_on_one_line_is_furniture() -> None:
    """A margin line like "V. 23" (head + folio) is excluded as furniture."""

    pages: dict[int, PageOCRResult] = {}
    for index in range(4):
        pages[index] = _page(
            [
                _line(f"V. {index + 1}", y=0.02, height=0.012),
                _line("正文", y=0.2),
            ],
            page_index=index,
        )

    detected = detect_page_furniture(pages)

    # The folio part makes each line different, so it is not "repeated text",
    # but the head "V." repeats on all pages, so the whole line is furniture.
    for found in detected.values():
        assert 0 in found
        assert found[0] == REASON_PAGE_NUMBER


def test_alternating_odd_even_running_heads_are_detected() -> None:
    """Odd pages carry the book title, even pages the part title; both removed."""

    pages: dict[int, PageOCRResult] = {}
    for index in range(6):
        head = "书名" if index % 2 == 0 else "第一章"
        pages[index] = _page(
            [
                _line(head, y=0.02, height=0.012),
                _line("正文", y=0.2),
            ],
            page_index=index,
        )

    detected = detect_page_furniture(pages)

    for found in detected.values():
        assert 0 in found
        assert found[0] == REASON_REPEATED_IN_MARGIN


def test_isolated_number_in_body_is_not_folio() -> None:
    """A solitary number line in the body area is not treated as a page number."""

    pages: dict[int, PageOCRResult] = {}
    for index in range(4):
        pages[index] = _page(
            [
                _line("正文第一行", y=0.02, height=0.012),
                _line("42", y=0.5),  # isolated number in the body, not the margin
            ],
            page_index=index,
        )

    detected = detect_page_furniture(pages)

    assert all(1 not in found for found in detected.values())


# ------------------------------------------------------------------ integration


def test_assembly_excludes_furniture_and_reports_it(tmp_path: Path) -> None:
    """Running heads must not reach the export, and must be reported."""

    from scribeforge.domain.ocr import OCRToken
    from scribeforge.pipeline.assembly import assemble_verified_content
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", "v.pdf", "hash")

    for page_index in range(3):
        store.add_page(project_id, page_index, f"p{page_index}.png", 100, 150)
        box = BoundingBox(0.1, 0.2, 0.8, 0.02)
        head_box = BoundingBox(0.4, 0.02, 0.2, 0.012)
        lines = (
            OCRLine("托马斯·品钦", head_box, (OCRToken("托马斯·品钦", 0.9, head_box),)),
            OCRLine("正文。", box, (OCRToken("正文。", 0.9, box),)),
        )
        result = PageOCRResult(page_index, "primary", "1.0", lines)
        store.record_ocr(project_id, result)
        secondary = PageOCRResult(
            page_index, "secondary", "1.0", lines
        )
        store.record_ocr(project_id, secondary)

    from scribeforge.domain.alignment import align_pages
    from scribeforge.domain.risk import assess_page, review_candidates
    from scribeforge.pipeline.adjudication import adjudicate_verification

    for page in store.list_pages(project_id):
        primary_run = store.latest_engine_run(project_id, page.page_index, "primary")
        secondary_run = store.latest_engine_run(project_id, page.page_index, "secondary")
        primary = store.load_ocr_run(primary_run)
        secondary = store.load_ocr_run(secondary_run)
        alignment = align_pages(primary, secondary)
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        store.record_verification(primary_run, secondary_run, alignment, risks, candidates)
        # Only the body pair is adjudicated; the head is furniture and must not
        # need a decision to let the book assemble.
        store.append_decision(
            project_id=project_id,
            page_index=page.page_index,
            pair_index=1,
            selected_text="正文。",
            actor="automatic",
            evidence_refs=("test",),
        )
        adjudicate_verification(
            store,
            project_id,
            store.latest_verification_id(project_id, page.page_index),
        )

    book = assemble_verified_content(store, project_id)

    assert [page.lines for page in book.pages] == [("正文。",)] * 3
    assert len(book.dropped_furniture) == 3
    assert {item.text for item in book.dropped_furniture} == {"托马斯·品钦"}
    assert {item.reason for item in book.dropped_furniture} == {REASON_REPEATED_IN_MARGIN}


def test_review_queue_hides_furniture_pairs(tmp_path: Path) -> None:
    """Reviewers must not be asked to adjudicate a running head 562 times."""

    from scribeforge.domain.ocr import OCRToken
    from scribeforge.pipeline.adjudication import review_queue
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", "v.pdf", "hash")

    for page_index in range(3):
        store.add_page(project_id, page_index, f"p{page_index}.png", 100, 150)
        head_box = BoundingBox(0.4, 0.02, 0.2, 0.012)
        body_box = BoundingBox(0.1, 0.2, 0.8, 0.02)
        primary = PageOCRResult(
            page_index,
            "primary",
            "1.0",
            (
                OCRLine("V.", head_box, (OCRToken("V.", 0.9, head_box),)),
                OCRLine("鴉片", body_box, (OCRToken("鴉片", 0.9, body_box),)),
            ),
        )
        secondary = PageOCRResult(
            page_index,
            "secondary",
            "1.0",
            (
                OCRLine("W.", head_box, (OCRToken("W.", 0.9, head_box),)),
                OCRLine("雅片", body_box, (OCRToken("雅片", 0.9, body_box),)),
            ),
        )
        primary_run = store.record_ocr(project_id, primary)
        secondary_run = store.record_ocr(project_id, secondary)

        from scribeforge.domain.alignment import align_pages
        from scribeforge.domain.risk import assess_page, review_candidates

        alignment = align_pages(primary, secondary)
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        store.record_verification(primary_run, secondary_run, alignment, risks, candidates)

    queue = review_queue(store, project_id)

    # Both the head ("V." vs "W.") and the body ("鴉" vs "雅") disagree; only the
    # body pair is real content.
    assert queue
    assert all(item.pair_index == 1 for item in queue)
