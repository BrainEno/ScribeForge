"""Tests for direction-aware alignment (vertical / right-to-left pages).

A vertical page is not a horizontal page with different text: its *columns* are
the reading unit, so pairing has to use the X axis and the emitted order has to run
right-to-left. These tests pin both, and also pin that a horizontal page keeps
behaving exactly as before.
"""

from __future__ import annotations

import pytest

from scribeforge.domain.alignment import align_pages
from scribeforge.domain.direction import detect_page_direction, resolve_page_direction
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageDirection, PageOCRResult


def _column(text: str, *, x: float, y: float = 0.10, width: float = 0.05, height: float = 0.70) -> OCRLine:
    return OCRLine(text, BoundingBox(x, y, width, height), ())


def _row(text: str, *, y: float, x: float = 0.10, width: float = 0.70, height: float = 0.04) -> OCRLine:
    return OCRLine(text, BoundingBox(x, y, width, height), ())


def _page(
    lines: tuple[OCRLine, ...],
    *,
    engine: str = "engine",
    direction: PageDirection | None = None,
) -> PageOCRResult:
    """Build a page; passing ``direction`` marks it as engine-declared."""

    return PageOCRResult(
        0,
        engine,
        "1.0",
        lines,
        direction=direction or PageDirection.HORIZONTAL_LR,
        direction_declared=direction is not None,
    )


def _vertical_page(*, direction: PageDirection = PageDirection.VERTICAL_RL) -> PageOCRResult:
    """Three columns reported in reading order: 第一列 at the right edge.

    Right-to-left reading means the first reported column is the rightmost one,
    which is also the column a reader starts with.
    """

    return _page(
        (
            _column("第一列文字", x=0.70),
            _column("第二列文字", x=0.50),
            _column("第三列文字", x=0.30),
        ),
        direction=direction,
    )


# ------------------------------------------------------------------- detection


def test_geometry_detects_a_vertical_page() -> None:
    assert detect_page_direction(_vertical_page()) is not None


def test_geometry_detects_the_column_order() -> None:
    """First reported column on the right means right-to-left reading."""

    detected = detect_page_direction(_vertical_page())

    assert detected is PageDirection.VERTICAL_RL


def test_left_to_right_columns_are_detected_too() -> None:
    page = _page(
        (
            _column("第一列文字", x=0.30),
            _column("第二列文字", x=0.50),
            _column("第三列文字", x=0.70),
        )
    )


    assert detect_page_direction(page) is PageDirection.VERTICAL_LR


def test_horizontal_page_yields_no_vertical_opinion() -> None:
    page = _page((_row("第一行", y=0.1), _row("第二行", y=0.2), _row("第三行", y=0.3)))

    assert detect_page_direction(page) is None


def test_too_few_lines_is_not_enough_evidence() -> None:
    page = _page((_column("孤行", x=0.5),))

    assert detect_page_direction(page) is None


def test_a_single_tall_box_does_not_flip_a_horizontal_page() -> None:
    page = _page(
        (
            _row("第一行", y=0.1),
            _row("第二行", y=0.2),
            _row("第三行", y=0.3),
            _row("第四行", y=0.4),
            _column("圖", x=0.5),
        )
    )

    assert detect_page_direction(page) is None


# --------------------------------------------------------------- resolution


def test_a_vertical_declaration_beats_the_horizontal_default() -> None:
    primary = _page((_row("甲", y=0.1),), direction=PageDirection.VERTICAL_RL)
    secondary = _page((_row("甲", y=0.1),))

    assert resolve_page_direction(primary, secondary) is PageDirection.VERTICAL_RL


def test_agreed_non_default_direction_wins() -> None:
    primary = _page((), direction=PageDirection.VERTICAL_LR)
    secondary = _page((), direction=PageDirection.VERTICAL_LR)

    assert resolve_page_direction(primary, secondary) is PageDirection.VERTICAL_LR


def test_engine_declarations_beat_geometry() -> None:
    """An engine that says horizontal is trusted over a tall-box heuristic."""

    vertical_looking = _vertical_page()
    primary = PageOCRResult(
        0, "primary", "1.0", vertical_looking.lines,
        direction=PageDirection.HORIZONTAL_LR,
        direction_declared=True,
    )
    secondary = PageOCRResult(
        0, "secondary", "1.0", vertical_looking.lines,
        direction=PageDirection.HORIZONTAL_LR,
        direction_declared=True,
    )

    assert resolve_page_direction(primary, secondary) is PageDirection.HORIZONTAL_LR


def test_geometry_is_the_fallback_when_both_engines_stay_default() -> None:
    primary = _vertical_page()
    secondary = _vertical_page()

    assert resolve_page_direction(primary, secondary) is PageDirection.VERTICAL_RL


def test_no_evidence_falls_back_to_horizontal() -> None:
    primary = _page(())
    secondary = _page(())

    assert resolve_page_direction(primary, secondary) is PageDirection.HORIZONTAL_LR


# ------------------------------------------------------------------ alignment


def test_vertical_columns_are_paired_by_x_not_y() -> None:
    """The original bug: every column shared one Y centre, so Y-pairing merged them.

    With all three columns at the same vertical position, a Y-axis matcher cannot
    tell them apart and pairs them by text luck alone; an X-axis matcher pairs each
    column with its counterpart.
    """

    primary = _vertical_page()
    secondary = _vertical_page()

    alignment = align_pages(primary, secondary)

    assert alignment.direction is PageDirection.VERTICAL_RL
    assert len(alignment.pairs) == 3
    for pair in alignment.pairs:
        assert pair.primary_line_index == pair.secondary_line_index


def test_vertical_alignment_reports_the_cross_axis_gap() -> None:
    """The distance is measured across the reading axis, i.e. horizontally here."""

    primary = _vertical_page()
    secondary = _page(
        (
            _column("第一列文字", x=0.72),
            _column("第二列文字", x=0.52),
            _column("第三列文字", x=0.32),
        ),
        direction=PageDirection.VERTICAL_RL,
    )

    alignment = align_pages(primary, secondary)

    gaps = [pair.cross_axis_distance for pair in alignment.pairs]
    assert all(gap is not None and gap == pytest.approx(0.02, abs=1e-6) for gap in gaps)


def test_vertical_pairs_are_emitted_right_to_left() -> None:
    """Reading order must start at the rightmost column."""

    primary = _vertical_page()
    secondary = _vertical_page()

    alignment = align_pages(primary, secondary)

    texts = [primary.lines[pair.primary_line_index].text for pair in alignment.pairs]
    assert texts == ["第一列文字", "第二列文字", "第三列文字"]


def test_left_to_right_vertical_columns_are_emitted_left_to_right() -> None:
    page = _page(
        (
            _column("第一列文字", x=0.30),
            _column("第二列文字", x=0.50),
            _column("第三列文字", x=0.70),
        ),
        direction=PageDirection.VERTICAL_LR,
    )

    alignment = align_pages(page, page)

    texts = [page.lines[pair.primary_line_index].text for pair in alignment.pairs]
    assert texts == ["第一列文字", "第二列文字", "第三列文字"]


def test_direction_can_be_supplied_by_the_caller() -> None:
    """Page-level evidence elsewhere may decide the direction before alignment."""

    primary = _page((_column("甲列", x=0.7), _column("乙列", x=0.5)))
    secondary = _page((_column("甲列", x=0.7), _column("乙列", x=0.5)))


    alignment = align_pages(primary, secondary, direction=PageDirection.VERTICAL_RL)

    assert alignment.direction is PageDirection.VERTICAL_RL
    texts = [primary.lines[pair.primary_line_index].text for pair in alignment.pairs]
    assert texts == ["甲列", "乙列"]


def test_horizontal_reading_order_is_unchanged() -> None:
    primary = _page((_row("第一行", y=0.10), _row("第二行", y=0.20)))
    secondary = _page((_row("第一行", y=0.10), _row("第二行", y=0.20)))

    alignment = align_pages(primary, secondary)

    assert alignment.direction is PageDirection.HORIZONTAL_LR
    texts = [primary.lines[pair.primary_line_index].text for pair in alignment.pairs]
    assert texts == ["第一行", "第二行"]


def test_reading_order_is_applied_even_when_the_engine_reports_out_of_order() -> None:
    """Engines report columns in arbitrary order; export must not inherit that."""

    primary = _page(
        (
            _column("第三列文字", x=0.30),
            _column("第一列文字", x=0.70),
            _column("第二列文字", x=0.50),
        ),
        direction=PageDirection.VERTICAL_RL,
    )

    alignment = align_pages(primary, primary)

    texts = [primary.lines[pair.primary_line_index].text for pair in alignment.pairs]
    assert texts == ["第一列文字", "第二列文字", "第三列文字"]


def test_unmatched_vertical_line_is_kept_and_ordered() -> None:
    primary = _page(
        (
            _column("第一列文字", x=0.70),
            _column("第二列文字", x=0.50),
            _column("第三列文字", x=0.30),
        ),
        direction=PageDirection.VERTICAL_RL,
    )
    secondary = _page(
        (_column("第一列文字", x=0.70), _column("第二列文字", x=0.50)),
        direction=PageDirection.VERTICAL_RL,
    )

    alignment = align_pages(primary, secondary)

    assert len(alignment.pairs) == 3
    missing = [pair for pair in alignment.pairs if pair.secondary_line_index is None]
    assert len(missing) == 1
    assert primary.lines[missing[0].primary_line_index].text == "第三列文字"
    # The unmatched column still sits in its reading position, not at the end.
    assert alignment.pairs[-1].secondary_line_index is None


def test_alignment_rejects_a_non_positive_threshold() -> None:
    page = _page((_row("甲", y=0.1),))

    with pytest.raises(ValueError, match="threshold must be positive"):
        align_pages(page, page, max_cross_axis_distance=0.0)
