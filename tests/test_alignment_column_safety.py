from __future__ import annotations

import pytest

from scribeforge.domain.alignment import align_pages
from scribeforge.domain.ocr import (
    BlockType,
    BoundingBox,
    OCRBlock,
    OCRLine,
    PageDirection,
    PageOCRResult,
)


def _result(engine: str, boxes: list[BoundingBox], roles: tuple[BlockType, ...] = ()) -> PageOCRResult:
    lines = tuple(OCRLine("相同文字", box) for box in boxes)
    blocks = tuple(OCRBlock(role, box, i, i + 1) for i, (role, box) in enumerate(zip(roles, boxes)))
    return PageOCRResult(0, engine, "1", lines, blocks)


@pytest.mark.parametrize("direction", [PageDirection.HORIZONTAL_LR, PageDirection.HORIZONTAL_RL])
def test_missing_left_column_does_not_shift_matches(direction: PageDirection) -> None:
    left = BoundingBox(0.1, 0.2, 0.3, 0.05)
    right = BoundingBox(0.6, 0.2, 0.3, 0.05)
    primary = _result("a", [left, right])
    secondary = _result("b", [right])
    pairs = align_pages(primary, secondary, direction=direction).pairs
    assert {(p.primary_line_index, p.secondary_line_index) for p in pairs} == {(0, None), (1, 0)}


@pytest.mark.parametrize("direction", [PageDirection.VERTICAL_RL, PageDirection.VERTICAL_LR])
def test_missing_top_region_does_not_shift_vertical_matches(direction: PageDirection) -> None:
    top = BoundingBox(0.5, 0.1, 0.05, 0.3)
    bottom = BoundingBox(0.5, 0.6, 0.05, 0.3)
    primary = _result("a", [top, bottom])
    secondary = _result("b", [bottom])
    pairs = align_pages(primary, secondary, direction=direction).pairs
    assert {(p.primary_line_index, p.secondary_line_index) for p in pairs} == {(0, None), (1, 0)}


@pytest.mark.parametrize("role", [BlockType.TITLE, BlockType.FOOTNOTE, BlockType.HEADER, BlockType.FOOTER])
def test_known_incompatible_block_roles_are_never_paired(role: BlockType) -> None:
    box = BoundingBox(0.1, 0.2, 0.3, 0.05)
    primary = _result("a", [box], (BlockType.TEXT,))
    secondary = _result("b", [box], (role,))
    pairs = align_pages(primary, secondary, direction=PageDirection.HORIZONTAL_LR).pairs
    assert {(p.primary_line_index, p.secondary_line_index) for p in pairs} == {(0, None), (None, 0)}


def test_equal_column_candidates_are_left_unmatched() -> None:
    # Narrow neighbours have equal claim on a spanning OCR line: do not guess.
    primary = _result("a", [BoundingBox(0.32, 0.2, 0.04, 0.05), BoundingBox(0.44, 0.2, 0.04, 0.05)])
    secondary = _result("b", [BoundingBox(0.32, 0.2, 0.16, 0.05)])
    pairs = align_pages(primary, secondary, direction=PageDirection.HORIZONTAL_LR).pairs
    assert {(p.primary_line_index, p.secondary_line_index) for p in pairs} == {
        (0, None), (1, None), (None, 0)
    }


def test_nearby_nonoverlapping_columns_are_not_cross_matched() -> None:
    primary = _result("a", [BoundingBox(0.1, 0.2, 0.04, 0.05)])
    secondary = _result("b", [BoundingBox(0.18, 0.2, 0.04, 0.05)])
    pairs = align_pages(primary, secondary, direction=PageDirection.HORIZONTAL_LR).pairs
    assert {(p.primary_line_index, p.secondary_line_index) for p in pairs} == {(0, None), (None, 0)}


def test_unmatched_conflicting_roles_become_review_candidates() -> None:
    from scribeforge.domain.risk import RiskReason, assess_page, review_candidates

    box = BoundingBox(0.1, 0.2, 0.3, 0.05)
    primary = _result("a", [box], (BlockType.TEXT,))
    secondary = _result("b", [box], (BlockType.FOOTNOTE,))
    result = align_pages(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    risks = assess_page(result, primary, secondary)
    candidates = review_candidates(result, risks, primary, secondary)
    assert {candidate.pair_index for candidate in candidates} == {0, 1}
    assert all(RiskReason.MISSING_OR_EXTRA_LINE in risk.reasons for risk in risks)
    assert {(p.primary_line_index, p.secondary_line_index) for p in result.pairs} == {(0, None), (None, 0)}


def test_recorded_unknown_or_overlapping_roles_are_not_guessed() -> None:
    box = BoundingBox(0.1, 0.2, 0.3, 0.05)
    primary = _result("a", [box], (BlockType.TEXT,))
    unknown = _result("b", [box], (BlockType.UNKNOWN,))
    conflicting = PageOCRResult(0, "b", "1", unknown.lines, (
        OCRBlock(BlockType.TEXT, box, 0, 1), OCRBlock(BlockType.TITLE, box, 0, 1),
    ))
    for secondary in (unknown, conflicting):
        pairs = align_pages(primary, secondary, direction=PageDirection.HORIZONTAL_LR).pairs
        assert {(p.primary_line_index, p.secondary_line_index) for p in pairs} == {(0, None), (None, 0)}


def test_repeated_200_line_fixture_is_deterministic() -> None:
    boxes = [BoundingBox(0.1, 0.002 + i * 0.0045, 0.4, 0.003) for i in range(200)]
    def page(engine: str) -> PageOCRResult:
        return PageOCRResult(0, engine, "1", tuple(
            OCRLine(f"line {i:03d}", box) for i, box in enumerate(boxes)
        ))
    primary, secondary = page("a"), page("b")
    first = align_pages(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    second = align_pages(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    assert first == second
    assert [(p.primary_line_index, p.secondary_line_index) for p in first.pairs] == [
        (i, i) for i in range(200)
    ]
