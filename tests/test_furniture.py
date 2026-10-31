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
    PageDirection,
    PageOCRResult,
)


def _line(
    text: str,
    *,
    x: float = 0.1,
    y: float = 0.1,
    width: float = 0.8,
    height: float = 0.02,
) -> OCRLine:
    return OCRLine(text, BoundingBox(x, y, width, height), ())


def _page(
    lines: list[OCRLine],
    *,
    page_index: int,
    blocks: tuple[OCRBlock, ...] = (),
    direction=None,
) -> PageOCRResult:
    return PageOCRResult(
        page_index=page_index,
        engine="fixture",
        engine_version="1.0",
        lines=tuple(lines),
        blocks=blocks,
        direction=direction or PageDirection.HORIZONTAL_LR,
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
    """A repeated V. stays a running head, not a stationary folio sequence."""

    pages = {
        index: _page(
            [_line("V.", y=0.02, height=0.012), _line("正文", y=0.2)], page_index=index
        )
        for index in range(3)
    }

    detected = detect_page_furniture(pages)

    assert all(found[0] == REASON_REPEATED_IN_MARGIN for found in detected.values())


def test_folios_require_a_consistent_physical_page_sequence() -> None:
    """A number at the page edge is not a folio unless nearby labels progress."""

    numbers = {0: "3", 1: "18", 2: "26"}
    pages = {
        page_index: _page(
            [
                _line("正文第一行", y=0.2),
                _line(number, y=0.96, height=0.012),
            ],
            page_index=page_index,
        )
        for page_index, number in numbers.items()
    }

    assert detect_page_furniture(pages) == {}

    # A stable but non-progressing OCR hallucination is not promoted to a folio
    # merely because it recurs at the same edge on several pages.
    repeated = {
        page_index: _page(
            [_line("正文第一行", y=0.2), _line("7", y=0.96, height=0.012)],
            page_index=page_index,
        )
        for page_index in range(3)
    }
    assert detect_page_furniture(repeated) == {}


def test_sparse_v_benchmark_indexes_remove_running_head_and_continuous_folios() -> None:
    """Sparse manifest page indexes preserve physical-page continuity evidence."""

    # These are the known folios on manifest cases v-020/v-030/v-040. Their
    # physical page indexes are sparse, and the printed labels progress by ten.
    folios = {19: "3", 29: "13", 39: "23"}
    page_indexes = (9, 13, 17, 19, 29, 39)
    pages = {
        page_index: _page(
            [
                _line("V.", y=0.02, height=0.012),
                _line("正文第一行", y=0.2),
                *(
                    [_line(folios[page_index], y=0.96, height=0.012)]
                    if page_index in folios
                    else []
                ),
            ],
            page_index=page_index,
        )
        for page_index in page_indexes
    }

    detected = detect_page_furniture(pages)

    assert detected == {
        page_index: (
            {0: REASON_REPEATED_IN_MARGIN, 2: REASON_PAGE_NUMBER}
            if page_index in folios
            else {0: REASON_REPEATED_IN_MARGIN}
        )
        for page_index in page_indexes
    }


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


def test_vertical_running_head_in_side_margin_is_detected() -> None:
    """A vertical book's running head sits in the left/right margin, not top/bottom.

    For vertical text the margin band is the side edge, so a line at x=0.02
    (left edge) repeating on all pages is furniture even though it is far from
    the top/bottom edge.
    """

    pages: dict[int, PageOCRResult] = {}
    for index in range(4):
        pages[index] = _page(
            [
                # Left-side margin line (would be body text in horizontal layout).
                _line("托马斯·品钦", x=0.02, y=0.2, width=0.012, height=0.4),
                _line("正文第一行", x=0.15, y=0.2),
                _line("正文第二行", x=0.15, y=0.35),
            ],
            page_index=index,
            direction=PageDirection.VERTICAL_RL,
        )

    detected = detect_page_furniture(pages)

    for found in detected.values():
        assert 0 in found
        assert found[0] == REASON_REPEATED_IN_MARGIN


def test_vertical_body_line_away_from_side_margin_is_not_furniture() -> None:
    """A vertical page's body line is not furniture just because it is near top/bottom.

    For vertical text, top/bottom proximity does not make a line a margin line;
    only left/right proximity does.
    """

    pages: dict[int, PageOCRResult] = {}
    for index in range(4):
        pages[index] = _page(
            [
                _line("托马斯·品钦", x=0.02, y=0.2, width=0.012, height=0.4),
                # Near the top edge but in the body horizontally — not furniture.
                _line("正文第一行", x=0.15, y=0.02, width=0.4, height=0.012),
                _line("正文第二行", x=0.15, y=0.15),
            ],
            page_index=index,
            direction=PageDirection.VERTICAL_RL,
        )

    detected = detect_page_furniture(pages)

    assert all(0 in found for found in detected.values())
    assert all(1 not in found for found in detected.values())


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


def test_assembly_excludes_furniture_and_can_preserve_it(tmp_path: Path) -> None:
    """Six-page V. fixture drops headers/folios by default, but can keep them."""

    from scribeforge.domain.alignment import align_pages
    from scribeforge.domain.ocr import OCRToken
    from scribeforge.domain.risk import assess_page, review_candidates
    from scribeforge.pipeline.adjudication import adjudicate_verification
    from scribeforge.pipeline.assembly import assemble_verified_content
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", "v.pdf", "hash")

    for page_index in range(6):
        store.add_page(project_id, page_index, f"p{page_index}.png", 100, 150)
        body_box = BoundingBox(0.1, 0.2, 0.8, 0.02)
        head_box = BoundingBox(0.4, 0.02, 0.2, 0.012)
        folio_box = BoundingBox(0.4, 0.96, 0.2, 0.012)
        folio = str(page_index + 3)
        lines = (
            OCRLine("V.", head_box, (OCRToken("V.", 0.9, head_box),)),
            OCRLine("正文。", body_box, (OCRToken("正文。", 0.9, body_box),)),
            OCRLine(folio, folio_box, (OCRToken(folio, 0.9, folio_box),)),
        )
        primary = PageOCRResult(page_index, "primary", "1.0", lines)
        secondary = PageOCRResult(page_index, "secondary", "1.0", lines)
        primary_run = store.record_ocr(project_id, primary)
        secondary_run = store.record_ocr(project_id, secondary)
        alignment = align_pages(primary, secondary)
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        verification_id = store.record_verification(
            primary_run, secondary_run, alignment, risks, candidates
        )
        store.append_decision(
            project_id=project_id,
            page_index=page_index,
            pair_index=1,
            selected_text="正文。",
            actor="automatic",
            evidence_refs=("test",),
        )
        adjudicate_verification(store, project_id, verification_id)

    filtered = assemble_verified_content(store, project_id)
    preserved = assemble_verified_content(
        store, project_id, include_page_furniture=True
    )

    assert [page.lines for page in filtered.pages] == [("正文。",)] * 6
    assert len(filtered.dropped_furniture) == 12
    assert {item.text for item in filtered.dropped_furniture} == {
        "V.", "3", "4", "5", "6", "7", "8"
    }
    assert {item.reason for item in filtered.dropped_furniture} == {
        REASON_REPEATED_IN_MARGIN, REASON_PAGE_NUMBER
    }
    assert [page.lines for page in preserved.pages] == [
        ("V.", "正文。", str(page_index + 3)) for page_index in range(6)
    ]
    assert preserved.dropped_furniture == ()


def test_furniture_line_indexes_do_not_hide_secondary_body_lines(tmp_path: Path) -> None:
    """A secondary index must not be compared to primary-run furniture indexes."""

    from scribeforge.domain.alignment import align_pages
    from scribeforge.domain.ocr import OCRToken
    from scribeforge.domain.risk import assess_page, review_candidates
    from scribeforge.pipeline.adjudication import review_queue
    from scribeforge.pipeline.rounds import open_pairs
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
                OCRLine("正文甲", body_box, (OCRToken("正文甲", 0.9, body_box),)),
            ),
        )
        secondary = PageOCRResult(
            page_index,
            "secondary",
            "1.0",
            (OCRLine("正文乙", body_box, (OCRToken("正文乙", 0.9, body_box),)),),
        )
        primary_run = store.record_ocr(project_id, primary)
        secondary_run = store.record_ocr(project_id, secondary)
        alignment = align_pages(primary, secondary)
        assert (
            alignment.pairs[1].primary_line_index,
            alignment.pairs[1].secondary_line_index,
        ) == (
            1,
            0,
        )
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        store.record_verification(primary_run, secondary_run, alignment, risks, candidates)

    queue = review_queue(store, project_id)

    assert len(queue) == 3
    assert all(item.pair_index == 1 for item in queue)
    assert len(open_pairs(store, project_id)) == 3
    assert len(open_pairs(store, project_id, include_page_furniture=True)) == 6


def test_furniture_line_indexes_do_not_hide_secondary_body_lines_in_export(
    tmp_path: Path,
) -> None:
    from scribeforge.domain.alignment import align_pages
    from scribeforge.domain.ocr import OCRToken
    from scribeforge.domain.risk import assess_page, review_candidates
    from scribeforge.pipeline.assembly import assemble_verified_content
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
                OCRLine("正文", body_box, (OCRToken("正文", 0.9, body_box),)),
            ),
        )
        secondary = PageOCRResult(
            page_index,
            "secondary",
            "1.0",
            (OCRLine("正文", body_box, (OCRToken("正文", 0.9, body_box),)),),
        )
        primary_run = store.record_ocr(project_id, primary)
        secondary_run = store.record_ocr(project_id, secondary)
        alignment = align_pages(primary, secondary)
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        store.record_verification(primary_run, secondary_run, alignment, risks, candidates)
        store.append_decision(
            project_id=project_id,
            page_index=page_index,
            pair_index=1,
            selected_text="正文",
            actor="human",
            evidence_refs=("test",),
        )

    book = assemble_verified_content(store, project_id)

    assert [page.lines for page in book.pages] == [("正文",)] * 3
    assert len(book.dropped_furniture) == 3
    assert {item.text for item in book.dropped_furniture} == {"V."}


def test_review_queue_hides_furniture_but_allows_reviewing_it(tmp_path: Path) -> None:
    """A six-page fake-OCR fixture measures filtering and opt-in review."""

    from scribeforge.domain.alignment import align_pages
    from scribeforge.domain.ocr import OCRToken
    from scribeforge.domain.risk import assess_page, review_candidates
    from scribeforge.pipeline.adjudication import (
        adjudicate_verification,
        review_detail,
        review_queue,
    )
    from scribeforge.pipeline.rounds import open_pairs, progress
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", "v.pdf", "hash")

    for page_index in range(6):
        store.add_page(project_id, page_index, f"p{page_index}.png", 100, 150)
        head_box = BoundingBox(0.4, 0.02, 0.2, 0.012)
        body_box = BoundingBox(0.1, 0.2, 0.8, 0.02)
        folio_box = BoundingBox(0.4, 0.96, 0.2, 0.012)
        folio = str(page_index + 3)
        primary = PageOCRResult(
            page_index,
            "primary",
            "1.0",
            (
                OCRLine("V.", head_box, (OCRToken("V.", 0.9, head_box),)),
                OCRLine("鴉片", body_box, (OCRToken("鴉片", 0.9, body_box),)),
                OCRLine(folio, folio_box, (OCRToken(folio, 0.9, folio_box),)),
            ),
        )
        secondary = PageOCRResult(
            page_index,
            "secondary",
            "1.0",
            (
                OCRLine("W.", head_box, (OCRToken("W.", 0.9, head_box),)),
                OCRLine("雅片", body_box, (OCRToken("雅片", 0.9, body_box),)),
                OCRLine(folio, folio_box, (OCRToken(folio, 0.9, folio_box),)),
            ),
        )
        primary_run = store.record_ocr(project_id, primary)
        secondary_run = store.record_ocr(project_id, secondary)
        alignment = align_pages(primary, secondary)
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        verification_id = store.record_verification(
            primary_run, secondary_run, alignment, risks, candidates
        )
        adjudicate_verification(store, project_id, verification_id)

    filtered_queue = review_queue(store, project_id)
    complete_queue = review_queue(store, project_id, include_page_furniture=True)

    # The mismatched header ("V." vs "W.") and the body ("鴉" vs "雅") remain
    # reviewable, while aligned folios are accepted and never add queue noise.
    assert len(filtered_queue) == 6
    assert all(item.pair_index == 1 for item in filtered_queue)
    assert len(complete_queue) == 12
    assert len(open_pairs(store, project_id)) == 6
    assert len(open_pairs(store, project_id, include_page_furniture=True)) == 12
    assert progress(store, project_id).pairs == 6
    assert progress(store, project_id, include_page_furniture=True).pairs == 18
    furniture_item = next(
        item for item in complete_queue if item.page_index == 0 and item.pair_index == 0
    )
    assert furniture_item.furniture_reason == REASON_REPEATED_IN_MARGIN
    detail = review_detail(
        store,
        project_id,
        furniture_item.page_index,
        furniture_item.pair_index,
        furniture_reason=furniture_item.furniture_reason,
    )
    assert detail.primary_text == "V."
    assert detail.secondary_text == "W."
    assert detail.furniture_reason == REASON_REPEATED_IN_MARGIN
    assert (
        review_detail(
            store, project_id, furniture_item.page_index, furniture_item.pair_index
        ).furniture_reason
        == REASON_REPEATED_IN_MARGIN
    )

    import io
    import json

    from scribeforge.cli import run

    output = io.StringIO()
    assert (
        run(
            [
                "review",
                "--store",
                str(tmp_path),
                "--include-page-furniture",
                "--json",
            ],
            stdout=output,
        )
        == 0
    )
    review_items = json.loads(output.getvalue())["projects"][0]["items"]
    assert any(
        item["furniture_reason"] == REASON_REPEATED_IN_MARGIN
        and item["primary_text"] == "V."
        and item["secondary_text"] == "W."
        for item in review_items
    )
