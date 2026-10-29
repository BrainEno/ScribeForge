"""PaddleOCR-VL worker (technology route R1).

Prints one contract-v2 document on stdout, exactly like ``paddle_worker.py``, so
the adapter, the pipeline and the benchmark need no new code path.

Honesty note: this environment has no GPU and no model download channel, so the
invocation below and the JSON field names are taken from the PaddleOCR 3.x
pipeline documentation and have **not** been executed against real weights. The
acceptance gate R1 in ``docs/technology-route.md`` requires that real run before
this backend can be claimed as working. The parser is deliberately tolerant: an
unknown field shape degrades to "no block evidence" instead of crashing a page,
and the direction is reported only when the payload actually asserts one.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scribeforge.workers.protocol import (
    block_document,
    canonical_block_label,
    clamp_box,
    normalize_polygon,
    reported_direction,
    result_document,
)

ENGINE_NAME = "paddleocr-vl"
ENGINE_VERSION_FALLBACK = "0.9b"

#: Runs the layout-parsing pipeline and writes one JSON document per page. The
#: worker shells out to a separate interpreter so importing PaddlePaddle can
#: never contaminate the application process.
PREDICT_SCRIPT = """
import json, sys
from paddleocr import PaddleOCRVL

image, output_dir = sys.argv[1], sys.argv[2]
pipeline = PaddleOCRVL()
for result in pipeline.predict(input=image):
    result.save_to_json(save_path=output_dir)
"""

_REGION_KEYS = ("parsing_res_list", "layout_parsing_result", "blocks", "regions")


def build_command(image: str, output_dir: Path) -> list[str]:
    return [sys.executable, "-c", PREDICT_SCRIPT, image, str(output_dir)]


def _page_document(raw: Any) -> dict[str, Any]:
    """Unwrap a single-page result from the several shapes the CLI can emit."""

    if isinstance(raw, list):
        if not raw:
            raise ValueError("PaddleOCR-VL output is empty")
        raw = raw[0]
    if not isinstance(raw, dict):
        raise TypeError("unexpected PaddleOCR-VL JSON output")
    return raw


def _regions(document: dict[str, Any]) -> list[dict[str, Any]]:
    for key in _REGION_KEYS:
        value = document.get(key)
        if isinstance(value, list):
            regions = [item for item in value if isinstance(item, dict)]
            if regions:
                return regions
    return []


def _text_of(region: dict[str, Any]) -> str:
    for key in ("block_content", "content", "text", "rec_text"):
        value = region.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _confidence_of(region: dict[str, Any]) -> float | None:
    for key in ("score", "confidence", "rec_score"):
        value = region.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _raw_box_of(region: dict[str, Any]) -> list[Any] | None:
    for key in ("block_bbox", "bbox", "box", "rec_poly"):
        value = region.get(key)
        if isinstance(value, list) and value:
            return value
    return None


def _polygon_points(raw_box: list[Any]) -> list[list[float]] | None:
    """Normalise the bbox shapes VL accepts: corner pair or point polygon."""

    if len(raw_box) == 4 and all(isinstance(value, (int, float)) for value in raw_box):
        x1, y1, x2, y2 = (float(value) for value in raw_box)
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    points: list[list[float]] = []
    for point in raw_box:
        if not isinstance(point, list) or len(point) != 2:
            return None
        if not all(isinstance(value, (int, float)) for value in point):
            return None
        points.append([float(point[0]), float(point[1])])
    return points or None


def _normalized_box(points: list[list[float]], width: float, height: float) -> list[float]:
    if all(0.0 <= value <= 1.0 for point in points for value in point):
        # Already normalized: convert the corner polygon through the same clamp
        # path so a box flush with the trim cannot produce an invalid page box.
        return clamp_box(normalize_polygon(points, 1.0, 1.0))
    return normalize_polygon(points, width, height)


def extract_page(
    raw: Any, *, width: float, height: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    """Turn a VL page result into ``(lines, blocks, direction)``.

    Lines are emitted in the order the model reports its regions, because that
    order *is* the model's reading order — re-sorting by geometry here would
    throw away the strongest vertical-text evidence the engine gives us.
    """

    document = _page_document(raw)
    direction = reported_direction(
        document.get("direction") or document.get("page_direction")
    )

    lines: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    for region in _regions(document):
        label = canonical_block_label(
            region.get("block_label") or region.get("label") or region.get("type")
        )
        raw_box = _raw_box_of(region)
        points = _polygon_points(raw_box) if raw_box is not None else None
        box = _normalized_box(points, width, height) if points is not None else None
        text = _text_of(region)
        if box is None:
            # No geometry means the region cannot participate in alignment;
            # its text would be unpairable noise, so it is dropped explicitly.
            continue
        if not text:
            # Structural regions without text (figures, tables) still carry
            # block evidence: they tell the furniture layer what not to read.
            blocks.append(block_document(label, box, 0, 0))
            continue
        confidence = _confidence_of(region)
        line_index = len(lines)
        lines.append(
            {
                "text": text,
                "box": box,
                "confidence": confidence,
                "tokens": [
                    {"text": text, "confidence": confidence, "box": box}
                ],
            }
        )
        blocks.append(block_document(label, box, line_index, line_index + 1))
    return lines, blocks, direction


def _page_size(path: str) -> tuple[float, float]:
    from PIL import Image

    with Image.open(path) as image:
        return float(image.width), float(image.height)


def _result_path(output_dir: Path) -> Path:
    candidates = sorted(output_dir.glob("*.json"))
    if not candidates:
        raise FileNotFoundError("PaddleOCR-VL produced no JSON output")
    return candidates[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--page-index", required=True, type=int)
    args = parser.parse_args()

    with TemporaryDirectory(prefix="scribeforge-paddle-vl-") as tmp:
        output_dir = Path(tmp)
        subprocess.run(
            build_command(args.image, output_dir),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        raw = json.loads(_result_path(output_dir).read_text(encoding="utf-8"))

    width, height = _page_size(args.image)
    lines, blocks, direction = extract_page(raw, width=width, height=height)
    document = result_document(
        ENGINE_NAME,
        ENGINE_VERSION_FALLBACK,
        args.page_index,
        lines,
        blocks=blocks,
        direction=direction,
    )
    sys.stdout.buffer.write(
        (json.dumps(document, ensure_ascii=False) + "\n").encode("utf-8")
    )
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
