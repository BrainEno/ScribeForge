from __future__ import annotations

from typing import Any


def normalize_polygon(points: list[list[float]], width: float, height: float) -> list[float]:
    if width <= 0 or height <= 0:
        raise ValueError("page dimensions must be positive")
    if not points:
        raise ValueError("polygon must not be empty")
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    return [left / width, top / height, (right - left) / width, (bottom - top) / height]


def result_document(engine: str, engine_version: str, page_index: int, lines: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": 1, "engine": engine, "engine_version": engine_version, "page_index": page_index, "lines": lines}
