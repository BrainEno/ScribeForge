"""Measure how much of a scanned page actually has ink on it.

This is the only *measurement* in the pipeline that can tell a blank leaf from a
page nobody managed to read. Everything else about a textless page is inference:
two engines reporting no lines is equally the signature of a crashed worker, a
mis-decoded raster and a genuinely empty leaf (issue #74). Ink coverage answers a
different question — "is there anything on this page to read at all?" — and it
answers it from the pixels the engines were given.

The measurement is deliberately coarse and deterministic: the page is reduced to a
bounded size, converted to luminance, and the share of pixels darker than a fixed
threshold is reported. It is not an image-quality metric and must not be used as
one; at this resolution it exists to separate ~0% (blank) from "there is a picture
here" (a plate) and from "there is a page of text here".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

#: Longest side kept for the measurement. Big enough that a woodblock page and a
#: sparse title page differ clearly, small enough for a 562-page book to stay cheap.
DEFAULT_MAX_DIMENSION = 512

#: Luminance below which a pixel counts as ink. Scans sit on a light background
#: with scanner noise; 200/255 keeps visible marks and drops the noise floor.
DEFAULT_INK_THRESHOLD = 200


@dataclass(frozen=True, slots=True)
class InkMeasurement:
    """Ink coverage of one page, with the parameters that produced it."""

    ratio: float
    width: int
    height: int
    max_dimension: int
    threshold: int

    def __post_init__(self) -> None:
        if not 0.0 <= self.ratio <= 1.0:
            raise ValueError("ink ratio must be between 0 and 1")


def measure_page_ink(
    path: Path | str,
    *,
    max_dimension: int = DEFAULT_MAX_DIMENSION,
    threshold: int = DEFAULT_INK_THRESHOLD,
) -> InkMeasurement:
    """Measure ink coverage of one page image.

    Raises ``OSError`` when the raster cannot be read (missing or damaged file);
    callers treat that as "no measurement", never as "blank".
    """

    if max_dimension < 1:
        raise ValueError("max_dimension must be positive")
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be between 0 and 255")

    with Image.open(Path(path)) as opened:
        grey = opened.convert("L")
        if max(grey.size) > max_dimension:
            scale = max_dimension / max(grey.size)
            size = (
                max(1, round(grey.width * scale)),
                max(1, round(grey.height * scale)),
            )
            grey = grey.resize(size)

    histogram = grey.histogram()
    total = sum(histogram)
    if not total:
        return InkMeasurement(0.0, grey.width, grey.height, max_dimension, threshold)
    dark = sum(histogram[:threshold])
    return InkMeasurement(
        ratio=dark / total,
        width=grey.width,
        height=grey.height,
        max_dimension=max_dimension,
        threshold=threshold,
    )
