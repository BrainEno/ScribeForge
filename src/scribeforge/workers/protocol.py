from __future__ import annotations

from typing import Any


def normalize_polygon(points: list[list[float]], width: float, height: float) -> list[float]:
    """Convert a pixel polygon into a normalized box clamped to the page.

    Real engines routinely return detection polygons that touch or spill a few
    pixels past the page edge (margins cropped by the scanner, deskewed images,
    blocks flush against the trim). Clamping keeps that evidence usable instead
    of rejecting the entire page because the geometry left the 0..1 range. A
    box that lands entirely outside the page collapses onto the nearest edge
    rather than describing area that the page does not contain.
    """

    if width <= 0 or height <= 0:
        raise ValueError("page dimensions must be positive")
    if not points:
        raise ValueError("polygon must not be empty")
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    # Clamp in pixel space so the normalized width/height stay the exact
    # difference of two in-range coordinates.
    left = min(max(min(xs), 0.0), width)
    right = min(max(max(xs), 0.0), width)
    top = min(max(min(ys), 0.0), height)
    bottom = min(max(max(ys), 0.0), height)
    return [
        left / width,
        top / height,
        (right - left) / width,
        (bottom - top) / height,
    ]


def result_document(engine: str, engine_version: str, page_index: int, lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": 1, "engine": engine, "engine_version": engine_version, "page_index": page_index, "lines": lines}
