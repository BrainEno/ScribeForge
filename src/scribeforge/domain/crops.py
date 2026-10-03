from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

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


def _floor_pixel(value: float) -> int:
    return math.floor(value + _PIXEL_EPSILON)


def _ceil_pixel(value: float) -> int:
    return math.ceil(value - _PIXEL_EPSILON)


def _pixel_box(
    candidate: ReviewCandidate,
    page_width: int,
    page_height: int,
    padding_fraction: float,
) -> PixelBox:
    box = candidate.crop
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
            box=_pixel_box(candidate, page_width, page_height, padding_fraction),
            file_name=(
                f"page-{candidate.page_index + 1:06d}-pair-{candidate.pair_index:04d}.png"
            ),
        )
        for candidate in candidates
    )
