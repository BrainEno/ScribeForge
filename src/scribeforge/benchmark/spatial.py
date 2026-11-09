"""Geometry-only line coverage metrics for the benchmark.

Strict LCS missing/extra lines answer a *textual* question: which whole lines
differ. A one-character substitution therefore shows up as one missing and one
extra line even though the page region was recognized. This module answers the
*spatial* question instead: given declared reference line boxes and the
hypothesis line boxes reported by an engine, which page regions have no
overlapping output line at all.

Matching is purely geometric (greedy one-to-one by intersection-over-union), so
a matched box does **not** prove the text matched, and a missing box does not
prove the text was wrong — the two metric families must be read together. Both
sides need boxes: without declared reference geometry the spatial counts are
unavailable and are reported as N/A rather than inferred from text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from scribeforge.domain.ocr import BoundingBox

DEFAULT_IOU_THRESHOLD = 0.5


def box_iou(first: BoundingBox, second: BoundingBox) -> float:
    """Intersection-over-union area of two normalized page boxes."""

    overlap_width = min(first.x + first.width, second.x + second.width) - max(
        first.x, second.x
    )
    overlap_height = min(first.y + first.height, second.y + second.height) - max(
        first.y, second.y
    )
    if overlap_width <= 0.0 or overlap_height <= 0.0:
        return 0.0
    intersection = overlap_width * overlap_height
    union = first.width * first.height + second.width * second.height - intersection
    if union <= 0.0:
        return 0.0
    # Clamp: floating-point area arithmetic can otherwise exceed 1.0 for
    # identical boxes, and IoU is definitionally within [0, 1].
    return min(1.0, max(0.0, intersection / union))


@dataclass(frozen=True, slots=True)
class SpatialLineMetrics:
    """Box-based line coverage; no text comparison is implied."""

    matched_lines: int
    missing_lines: int
    extra_lines: int


def spatial_line_metrics(
    reference_boxes: Sequence[BoundingBox],
    hypothesis_boxes: Sequence[BoundingBox],
    *,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> SpatialLineMetrics:
    """Greedily pair reference and hypothesis boxes with IoU >= ``iou_threshold``.

    Candidates are consumed in descending IoU order with deterministic index
    tie-breaks, and each box is used at most once, so duplicated boxes cannot
    invent matches. Unmatched reference boxes are spatially missing lines;
    unmatched hypothesis boxes are spatially extra lines.
    """

    if not 0.0 < iou_threshold <= 1.0:
        raise ValueError("iou_threshold must be in (0, 1]")
    candidates = [
        (box_iou(expected, actual), reference_index, hypothesis_index)
        for reference_index, expected in enumerate(reference_boxes)
        for hypothesis_index, actual in enumerate(hypothesis_boxes)
    ]
    candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_reference: set[int] = set()
    used_hypothesis: set[int] = set()
    matched = 0
    for iou, reference_index, hypothesis_index in candidates:
        if iou < iou_threshold:
            break
        if reference_index in used_reference or hypothesis_index in used_hypothesis:
            continue
        used_reference.add(reference_index)
        used_hypothesis.add(hypothesis_index)
        matched += 1
    return SpatialLineMetrics(
        matched_lines=matched,
        missing_lines=len(reference_boxes) - matched,
        extra_lines=len(hypothesis_boxes) - matched,
    )
