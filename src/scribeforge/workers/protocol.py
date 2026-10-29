from __future__ import annotations

from typing import Any

SUPPORTED_SCHEMA_VERSIONS = (1, 2)

#: Canonical structural labels shared by the worker contract and the domain
#: layer. Workers translate engine-specific labels into these values; anything
#: unrecognised becomes ``unknown`` so a new engine category never fails a page.
CANONICAL_BLOCK_LABELS = (
    "text",
    "title",
    "footnote",
    "figure",
    "table",
    "header",
    "footer",
    "formula",
    "marginalia",
    "unknown",
)

_ENGINE_LABEL_ALIASES = {
    "paragraph": "text",
    "heading": "title",
    "page_footnote": "footnote",
    "image": "figure",
    "page_header": "header",
    "page_footer": "footer",
    "interline_equation": "formula",
    "aside_text": "marginalia",
    "list": "text",
    "ref_text": "text",
}


def canonical_block_label(label: object) -> str:
    """Map an engine block label to a canonical label without ever raising."""

    if not isinstance(label, str):
        return "unknown"
    value = label.strip().lower()
    value = _ENGINE_LABEL_ALIASES.get(value, value)
    return value if value in CANONICAL_BLOCK_LABELS else "unknown"


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


def clamp_box(values: list[float]) -> list[float]:
    """Clamp an already-normalized ``[x, y, w, h]`` box into the page."""

    if len(values) != 4:
        raise ValueError("box must contain x, y, width, height")
    x, y, width, height = (float(value) for value in values)
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    width = min(max(width, 0.0), 1.0 - x)
    height = min(max(height, 0.0), 1.0 - y)
    return [x, y, width, height]


def result_document(
    engine: str,
    engine_version: str,
    page_index: int,
    lines: list[dict[str, Any]],
    *,
    blocks: list[dict[str, Any]] | None = None,
    direction: str = "horizontal-lr",
    schema_version: int = 2,
) -> dict[str, Any]:
    """Build the versioned JSON document a worker prints on stdout.

    Version 2 adds ``blocks`` (structural regions with a label and a line range)
    and ``direction`` (reading order). Version 1 remains supported by the adapter
    so an out-of-date worker degrades to "no block evidence" instead of failing.
    """

    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"unsupported worker schema version: {schema_version}")
    document: dict[str, Any] = {
        "schema_version": schema_version,
        "engine": engine,
        "engine_version": engine_version,
        "page_index": page_index,
        "lines": lines,
    }
    if schema_version >= 2:
        document["direction"] = direction
        document["blocks"] = blocks if blocks is not None else []
    return document


def block_document(
    block_type: str,
    box: list[float],
    line_start: int,
    line_end: int,
) -> dict[str, Any]:
    """Build one v2 block entry covering ``lines[line_start:line_end]``."""

    if line_start < 0 or line_end < line_start:
        raise ValueError("block line range must be a non-empty forward range")
    return {
        "type": block_type,
        "box": clamp_box(box),
        "line_start": line_start,
        "line_end": line_end,
    }
