from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from scribeforge.domain.ocr import BoundingBox
from scribeforge.domain.risk import ReviewCandidate

_PIXEL_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class PixelBox:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ValueError("pixel crop origin must be non-negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("pixel crop dimensions must be positive")


@dataclass(frozen=True, slots=True)
class ReviewCropPlan:
    page_index: int
    pair_index: int
    box: PixelBox
    file_name: str
    #: Factor the crop is enlarged by before it is shown or re-read. A reviewer
    #: zooming in and an OCR engine reading an isolated line both want the same
    #: thing: more pixels per stroke, not a different crop.
    scale: int = 1

    def __post_init__(self) -> None:
        if self.scale < 1:
            raise ValueError("crop scale must be a positive integer")


def region_read_file_name(page_index: int, pair_index: int, attempt: int) -> str:
    """Return the deterministic file name of a region re-read attempt.

    Attempts are numbered so round 3 never overwrites the crop round 1 was judged
    from: the older bytes are evidence that a pair was read again and still not
    settled.
    """

    if page_index < 0 or pair_index < 0:
        raise ValueError("crop indexes must be non-negative")
    if attempt < 1:
        raise ValueError("crop attempt must be one-based")
    return f"page-{page_index + 1:06d}-pair-{pair_index:04d}-read-{attempt:02d}.png"


def review_crop_file_name(page_index: int, pair_index: int) -> str:
    """Return the deterministic crop file name for a page pair.

    Shared by crop materialization and by the human-review surface so a queued
    pair can be traced back to the exact crop bytes a reviewer should inspect.
    """

    if page_index < 0 or pair_index < 0:
        raise ValueError("crop indexes must be non-negative")
    return f"page-{page_index + 1:06d}-pair-{pair_index:04d}.png"


def _floor_pixel(value: float) -> int:
    return math.floor(value + _PIXEL_EPSILON)


def _ceil_pixel(value: float) -> int:
    return math.ceil(value - _PIXEL_EPSILON)


def _pixel_box(
    box: BoundingBox,
    page_width: int,
    page_height: int,
    padding_fraction: float,
) -> PixelBox:
    horizontal_padding = page_width * padding_fraction
    vertical_padding = page_height * padding_fraction
    left = max(0, _floor_pixel(box.x * page_width - horizontal_padding))
    top = max(0, _floor_pixel(box.y * page_height - vertical_padding))
    right = min(
        page_width,
        _ceil_pixel((box.x + box.width) * page_width + horizontal_padding),
    )
    bottom = min(
        page_height,
        _ceil_pixel((box.y + box.height) * page_height + vertical_padding),
    )
    return PixelBox(left, top, right - left, bottom - top)


def plan_review_crops(
    candidates: Sequence[ReviewCandidate],
    page_width: int,
    page_height: int,
    *,
    padding_fraction: float = 0.01,
) -> tuple[ReviewCropPlan, ...]:
    if page_width <= 0 or page_height <= 0:
        raise ValueError("page dimensions must be positive")
    if padding_fraction < 0:
        raise ValueError("padding fraction must be non-negative")

    return tuple(
        ReviewCropPlan(
            page_index=candidate.page_index,
            pair_index=candidate.pair_index,
            box=_pixel_box(candidate.crop, page_width, page_height, padding_fraction),
            file_name=review_crop_file_name(candidate.page_index, candidate.pair_index),
        )
        for candidate in candidates
    )


def plan_region_read(
    *,
    page_index: int,
    pair_index: int,
    box: BoundingBox,
    page_width: int,
    page_height: int,
    attempt: int,
    padding_fraction: float,
    scale: int,
) -> ReviewCropPlan:
    """Plan a crop that re-reads one disputed line in isolation.

    The crop is wider than the line and may be enlarged, because that is the only
    thing a second pass can change: the same engine on the same pixels returns the
    same text. What it cannot fix is a stroke that both engines misread the same
    way — no amount of re-reading is a substitute for a different reader.
    """

    if page_width <= 0 or page_height <= 0:
        raise ValueError("page dimensions must be positive")
    if padding_fraction < 0:
        raise ValueError("padding fraction must be non-negative")
    if scale < 1:
        raise ValueError("crop scale must be a positive integer")

    return ReviewCropPlan(
        page_index=page_index,
        pair_index=pair_index,
        box=_pixel_box(box, page_width, page_height, padding_fraction),
        file_name=region_read_file_name(page_index, pair_index, attempt),
        scale=scale,
    )
