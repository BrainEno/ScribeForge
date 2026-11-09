import pytest

from scribeforge.benchmark.spatial import box_iou, spatial_line_metrics
from scribeforge.domain.ocr import BoundingBox


def test_box_iou_matches_known_geometries() -> None:
    box = BoundingBox(0.1, 0.1, 0.2, 0.2)
    assert box_iou(box, box) == 1.0
    assert box_iou(BoundingBox(0.0, 0.0, 0.2, 0.2), BoundingBox(0.5, 0.5, 0.2, 0.2)) == 0.0
    # Overlap 0.2 x 0.4 = 0.08 over union 0.16 + 0.16 - 0.08 = 0.24.
    assert box_iou(
        BoundingBox(0.0, 0.0, 0.4, 0.4), BoundingBox(0.2, 0.0, 0.4, 0.4)
    ) == pytest.approx(1 / 3)
    # A fully contained box: intersection 0.01 over union 0.16.
    assert box_iou(
        BoundingBox(0.0, 0.0, 0.4, 0.4), BoundingBox(0.1, 0.1, 0.1, 0.1)
    ) == pytest.approx(0.0625)


def test_spatial_line_metrics_count_exact_box_matches_as_matched() -> None:
    a = BoundingBox(0.05, 0.05, 0.9, 0.1)
    b = BoundingBox(0.05, 0.2, 0.9, 0.1)
    metrics = spatial_line_metrics([a, b], [a, b])

    assert (metrics.matched_lines, metrics.missing_lines, metrics.extra_lines) == (2, 0, 0)


def test_spatial_line_metrics_separate_missing_from_extra() -> None:
    a = BoundingBox(0.05, 0.05, 0.9, 0.1)
    b = BoundingBox(0.05, 0.2, 0.9, 0.1)
    far = BoundingBox(0.05, 0.8, 0.9, 0.1)

    metrics = spatial_line_metrics([a, b], [a, far])

    assert (metrics.matched_lines, metrics.missing_lines, metrics.extra_lines) == (1, 1, 1)


def test_spatial_line_metrics_respect_the_iou_threshold() -> None:
    base = BoundingBox(0.0, 0.0, 0.5, 0.1)
    shifted = BoundingBox(0.0, 0.06, 0.5, 0.1)  # IoU = 0.02 / 0.08 = 0.25

    strict = spatial_line_metrics([base], [shifted], iou_threshold=0.5)
    assert (strict.matched_lines, strict.missing_lines, strict.extra_lines) == (0, 1, 1)

    relaxed = spatial_line_metrics([base], [shifted], iou_threshold=0.2)
    assert (relaxed.matched_lines, relaxed.missing_lines, relaxed.extra_lines) == (1, 0, 0)


@pytest.mark.parametrize("threshold", [0.0, -0.1, 1.5])
def test_spatial_line_metrics_reject_invalid_thresholds(threshold: float) -> None:
    box = BoundingBox(0.0, 0.0, 0.5, 0.1)
    with pytest.raises(ValueError, match="iou_threshold"):
        spatial_line_metrics([box], [box], iou_threshold=threshold)


def test_spatial_line_metrics_match_one_to_one_greedily() -> None:
    first = BoundingBox(0.0, 0.0, 0.2, 0.2)
    overlapping = BoundingBox(0.05, 0.05, 0.2, 0.2)  # IoU with first = 0.0225 / 0.0575 < 0.5
    metrics = spatial_line_metrics([first, overlapping], [first])

    assert (metrics.matched_lines, metrics.missing_lines, metrics.extra_lines) == (1, 1, 0)


def test_spatial_line_metrics_handle_empty_sides() -> None:
    box = BoundingBox(0.0, 0.0, 0.5, 0.1)
    empty = spatial_line_metrics([], [])
    assert (empty.matched_lines, empty.missing_lines, empty.extra_lines) == (0, 0, 0)
    only_reference = spatial_line_metrics([box], [])
    assert (only_reference.missing_lines, only_reference.extra_lines) == (1, 0)
