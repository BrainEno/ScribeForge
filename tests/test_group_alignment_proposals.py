from __future__ import annotations

import pytest

from scribeforge.domain.ocr import BoundingBox, OCRLine, PageDirection, PageOCRResult


def page(engine: str, lines: list[tuple[str, tuple[float, float, float, float]]]) -> PageOCRResult:
    return PageOCRResult(0, engine, "1", tuple(OCRLine(text, BoundingBox(*box)) for text, box in lines))


@pytest.mark.parametrize("reverse", [False, True])
def test_horizontal_split_and_merge_proposals_preserve_every_index(reverse: bool) -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment

    joined = page("a", [("春眠不觉晓", (0.1, 0.2, 0.6, 0.05))])
    split = page("b", [("春眠", (0.1, 0.2, 0.24, 0.05)), ("不觉晓", (0.34, 0.2, 0.36, 0.05))])
    primary, secondary = (split, joined) if reverse else (joined, split)
    result = propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    group = result.groups[0]
    assert group.primary_indexes == ((0, 1) if reverse else (0,))
    assert group.secondary_indexes == ((0,) if reverse else (0, 1))
    assert group.status == "proposed"
    assert len(result.groups) == 1


@pytest.mark.parametrize("direction", [PageDirection.VERTICAL_RL, PageDirection.VERTICAL_LR])
def test_vertical_split_preserves_order_and_indices(direction: PageDirection) -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment

    joined = page("a", [("春眠不觉晓", (0.7, 0.1, 0.05, 0.6))])
    split = page("b", [("春眠", (0.7, 0.1, 0.05, 0.24)), ("不觉晓", (0.7, 0.34, 0.05, 0.36))])
    result = propose_group_alignment(joined, split, direction=direction)
    assert [(g.primary_indexes, g.secondary_indexes, g.status) for g in result.groups] == [
        ((0,), (0, 1), "proposed")
    ]


def test_ambiguous_duplicate_boxes_are_not_silently_paired() -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment

    line = ("甲", (0.1, 0.2, 0.4, 0.05))
    primary = page("a", [line])
    secondary = page("b", [line, line])
    result = propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    assert not any(g.status == "proposed" and g.primary_indexes and g.secondary_indexes for g in result.groups)
    assert any(g.status == "ambiguous" for g in result.groups)
    assert {i for g in result.groups for i in g.primary_indexes} == {0}
    assert {i for g in result.groups for i in g.secondary_indexes} == {0, 1}


def test_nonoverlapping_columns_do_not_form_a_group() -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment

    primary = page("a", [("甲乙", (0.1, 0.2, 0.2, 0.05))])
    secondary = page("b", [("甲", (0.6, 0.2, 0.1, 0.05)), ("乙", (0.7, 0.2, 0.1, 0.05))])
    result = propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    assert not any(g.primary_indexes and g.secondary_indexes for g in result.groups)


@pytest.mark.parametrize("role", ["title", "footnote", "header", "footer"])
def test_different_reported_regions_are_not_matched(role: str) -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment
    from scribeforge.domain.ocr import BlockType, OCRBlock

    box = BoundingBox(0.1, 0.2, 0.4, 0.05)
    lines = (OCRLine("相同", box),)
    primary = PageOCRResult(0, "a", "1", lines, (OCRBlock(BlockType.TEXT, box, 0, 1),))
    secondary = PageOCRResult(0, "b", "1", lines, (OCRBlock(BlockType(role), box, 0, 1),))
    result = propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    assert {(g.primary_indexes, g.secondary_indexes) for g in result.groups} == {((0,), ()), ((), (0,))}


def test_extra_line_remains_unmatched_and_labeled_matches_are_scored() -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment

    primary = page("a", [("甲", (0.1, 0.1, 0.4, 0.05)), ("乙", (0.1, 0.3, 0.4, 0.05))])
    secondary = page("b", [("甲", (0.1, 0.1, 0.4, 0.05)), ("新增", (0.1, 0.2, 0.4, 0.05)),
                            ("乙", (0.1, 0.3, 0.4, 0.05))])
    result = propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    expected = {((0,), (0,)), ((1,), (2,))}
    predicted = {(g.primary_indexes, g.secondary_indexes) for g in result.groups if g.status == "proposed"}
    tp = len(expected & predicted)
    precision, recall = tp / len(predicted), tp / len(expected)
    assert (precision, recall, 2 * precision * recall / (precision + recall)) == (1.0, 1.0, 1.0)
    assert [(g.primary_indexes, g.secondary_indexes) for g in result.groups] == [
        ((0,), (0,)), ((), (1,)), ((1,), (2,))
    ]


def test_proposals_are_deterministic_and_limit_is_enforced() -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment

    lines = [(str(i), (0.1, 0.002 + i * 0.004, 0.4, 0.003)) for i in range(200)]
    primary, secondary = page("a", lines), page("b", lines)
    a = propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    assert a == propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    assert [(g.primary_indexes, g.secondary_indexes) for g in a.groups] == [((i,), (i,)) for i in range(200)]
    with pytest.raises(ValueError, match="cell limit"):
        propose_group_alignment(page("a", lines * 3), page("b", lines * 3), direction=PageDirection.HORIZONTAL_LR)


@pytest.mark.parametrize("direction", [PageDirection.VERTICAL_RL, PageDirection.VERTICAL_LR])
def test_vertical_two_column_group_order_and_metrics(direction: PageDirection) -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment

    primary = page("a", [("甲乙", (0.2, 0.1, 0.05, 0.4)), ("甲乙", (0.7, 0.1, 0.05, 0.4))])
    secondary = page("b", [("甲", (0.2, 0.1, 0.05, 0.2)), ("乙", (0.2, 0.3, 0.05, 0.2)),
                            ("甲", (0.7, 0.1, 0.05, 0.2)), ("乙", (0.7, 0.3, 0.05, 0.2))])
    expected = [((1,), (2, 3)), ((0,), (0, 1))] if direction is PageDirection.VERTICAL_RL else [
        ((0,), (0, 1)), ((1,), (2, 3))
    ]
    result = propose_group_alignment(primary, secondary, direction=direction)
    actual = [(g.primary_indexes, g.secondary_indexes) for g in result.groups if g.status == "proposed"]
    assert actual == expected  # Includes both original line indexes and reading order.
    true_positives = len(set(expected) & set(actual))
    precision = true_positives / len(actual)
    recall = true_positives / len(expected)
    assert (precision, recall, 2 * precision * recall / (precision + recall)) == (1.0, 1.0, 1.0)
    assert sum(1 for p, s in actual if len(p) == 1 and len(s) == 2) == 2
    assert sum(1 for g in result.groups if g.status == "ambiguous") == 0
    assert sum(1 for p, s in actual if p[0] != s[0] // 2) == 0  # Cross-column errors.


def test_original_results_are_not_mutated_by_proposal() -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment

    primary = page("a", [("甲乙", (0.1, 0.2, 0.4, 0.05))])
    secondary = page("b", [("甲", (0.1, 0.2, 0.2, 0.05)), ("乙", (0.3, 0.2, 0.2, 0.05))])
    before = (primary, secondary)
    groups = propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    assert (primary, secondary) == before
    assert groups.groups[0].primary_indexes == (0,)
    assert groups.groups[0].secondary_indexes == (0, 1)


def test_split_fragments_in_distinct_blocks_are_not_combined() -> None:
    from scribeforge.domain.group_alignment import propose_group_alignment
    from scribeforge.domain.ocr import BlockType, OCRBlock

    primary = page("a", [("甲乙", (0.1, 0.2, 0.4, 0.05))])
    split = page("b", [("甲", (0.1, 0.2, 0.2, 0.05)), ("乙", (0.3, 0.2, 0.2, 0.05))])
    secondary = PageOCRResult(0, "b", "1", split.lines, (
        OCRBlock(BlockType.TEXT, split.lines[0].box, 0, 1),
        OCRBlock(BlockType.TEXT, split.lines[1].box, 1, 2),
    ))
    result = propose_group_alignment(primary, secondary, direction=PageDirection.HORIZONTAL_LR)
    assert not any(len(g.secondary_indexes) == 2 for g in result.groups)
