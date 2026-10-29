"""Surya structural worker (technology route R2).

Surya is wired as a **layout / reading-order second opinion**, not as a text
engine: it emits ``blocks`` and (when it can) a reading direction, and leaves
``lines`` empty. ``PageOCRResult.text_line_indexes()`` treats "no block evidence"
as "keep everything", so a structure-only engine can never delete text.

Honesty note: the upstream import paths and result field names below come from
the Surya documentation and have not been executed against real weights here
(no GPU, no model channel). Gate R2 in ``docs/technology-route.md`` requires a
real run plus a human spot check of block counts before this is claimed working.
The parser degrades to "no blocks" rather than failing a page on a field rename.

License note: Surya **code** is Apache-2.0 but its **weights** are OpenRAIL-M.
That distinction must be kept in any distributable packaging.
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
    result_document,
)

ENGINE_NAME = "surya"
ENGINE_VERSION_FALLBACK = "0.14"

PREDICT_SCRIPT = """
import json, sys
from PIL import Image
from surya.layout import LayoutPredictor

image_path, output_path = sys.argv[1], sys.argv[2]
image = Image.open(image_path).convert("RGB")
prediction = LayoutPredictor()([image])[0]
boxes = []
for item in prediction.bboxes:
    boxes.append(
        {
            "label": item.label,
            "bbox": list(item.bbox),
            "position": getattr(item, "position", None),
            "confidence": getattr(item, "confidence", None),
        }
    )
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump({"width": image.width, "height": image.height, "boxes": boxes}, handle)
"""


def build_command(image: str, output_path: Path) -> list[str]:
    return [sys.executable, "-c", PREDICT_SCRIPT, image, str(output_path)]


def _boxes(document: dict[str, Any]) -> list[dict[str, Any]]:
    raw = document.get("boxes") or document.get("bboxes") or document.get("layout")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _points(raw_box: Any) -> list[list[float]] | None:
    if not isinstance(raw_box, list):
        return None
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


def _page_size(document: dict[str, Any]) -> tuple[float, float] | None:
    width = document.get("width")
    height = document.get("height")
    if (
        isinstance(width, (int, float))
        and isinstance(height, (int, float))
        and width > 0
        and height > 0
    ):
        return float(width), float(height)
    return None


def extract_blocks(
    document: dict[str, Any], *, width: float | None = None, height: float | None = None
) -> list[dict[str, Any]]:
    """Build contract-v2 blocks in the model's own reading order.

    Surya returns a ``position`` per region; when it is present the blocks are
    ordered by it, which is the whole point of using Surya as a second opinion.
    Regions without usable geometry are dropped: a block that cannot be placed on
    the page cannot be reported as evidence about the page.
    """

    size = _page_size(document)
    page_width = size[0] if size is not None else width
    page_height = size[1] if size is not None else height

    ordered: list[tuple[float, int, dict[str, Any]]] = []
    for index, item in enumerate(_boxes(document)):
        position = item.get("position")
        order = float(position) if isinstance(position, (int, float)) else float(index)
        ordered.append((order, index, item))
    ordered.sort()

    blocks: list[dict[str, Any]] = []
    for _, _, item in ordered:
        points = _points(item.get("bbox") or item.get("box"))
        if points is None:
            continue
        if page_width and page_height:
            box = normalize_polygon(points, page_width, page_height)
        elif all(0.0 <= value <= 1.0 for point in points for value in point):
            box = clamp_box(normalize_polygon(points, 1.0, 1.0))
        else:
            # Pixel geometry without page dimensions cannot be normalized; skip
            # instead of guessing a frame that would land in the wrong place.
            continue
        label = canonical_block_label(item.get("label") or item.get("type"))
        blocks.append(block_document(label, box, 0, 0))
    return blocks


def reading_direction(blocks: list[dict[str, Any]]) -> str | None:
    """Report a direction only when block geometry is decisive.

    Surya does not classify page direction, so a direction here would be a
    geometric guess dressed up as an engine assertion. The pipeline already has
    ``detect_page_direction()`` for that, and a duplicate guess in the worker
    would override the domain's thresholds — so this deliberately returns
    ``None``.
    """

    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--page-index", required=True, type=int)
    args = parser.parse_args()

    with TemporaryDirectory(prefix="scribeforge-surya-") as tmp:
        output_path = Path(tmp) / "layout.json"
        subprocess.run(
            build_command(args.image, output_path),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        document = json.loads(output_path.read_text(encoding="utf-8"))

    blocks = extract_blocks(document)
    result = result_document(
        ENGINE_NAME,
        ENGINE_VERSION_FALLBACK,
        args.page_index,
        [],
        blocks=blocks,
        direction=reading_direction(blocks),
    )
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")
    )
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
