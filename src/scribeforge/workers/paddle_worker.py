from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scribeforge.workers.protocol import (
    canonical_block_label,
    clamp_box,
    normalize_polygon,
    reported_direction,
    result_document,
)


def build_command(image: str) -> list[str]:
    return [
        "paddleocr",
        "ocr",
        "--input",
        image,
        "--ocr_version",
        "PP-OCRv6",
        "--use_doc_orientation_classify",
        "False",
        "--use_doc_unwarping",
        "False",
        "--use_textline_orientation",
        "False",
    ]


def _image_size(path: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def extract_lines(
    raw: Any, image_size: tuple[int, int] | None = None
) -> list[dict[str, Any]]:
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if not isinstance(raw, dict):
        raise TypeError("unexpected PaddleOCR JSON output")

    texts: Any = raw.get("rec_texts", [])
    scores: Any = raw.get("rec_scores", [])
    polygons: Any = raw.get("rec_polys", raw.get("dt_polys", []))
    if not isinstance(texts, list) or not isinstance(scores, list) or not isinstance(polygons, list):
        raise TypeError("PaddleOCR result arrays must be lists")
    if not (len(texts) == len(scores) == len(polygons)):
        raise ValueError("PaddleOCR result arrays have inconsistent lengths")
    if any(not isinstance(polygon, list) for polygon in polygons):
        raise TypeError("PaddleOCR polygon must be a list")

    width_value: Any = raw.get("width", raw.get("image_width"))
    height_value: Any = raw.get("height", raw.get("image_height"))
    if width_value is None or height_value is None:
        if image_size is None:
            raise ValueError("PaddleOCR result is missing page dimensions")
        width_value, height_value = image_size
    width = float(width_value)
    height = float(height_value)

    lines: list[dict[str, Any]] = []
    for text, score, polygon in zip(texts, scores, polygons, strict=True):
        if not isinstance(polygon, list):
            raise TypeError("PaddleOCR polygon must be a list")
        box = normalize_polygon(polygon, width, height)
        lines.append(
            {
                "text": str(text),
                "box": box,
                "tokens": [
                    {
                        "text": str(text),
                        "confidence": float(score),
                        "box": box,
                    }
                ],
            }
        )
    return lines



def _engine_direction(raw: Any) -> Any:
    """Return PP-OCR's own direction claim, or ``None`` when it makes none.

    Plain PP-OCR reports text-line polygons and scores; it does not classify a
    page as vertical. Decorated attributes such as ``text_direction`` only show
    up when the corresponding subtype pipeline is enabled, so absence is normal.
    """

    if not isinstance(raw, dict):
        return None
    return raw.get("text_direction") or raw.get("direction")


def _layout_blocks(raw: Any) -> list[dict[str, Any]]:
    """Report structural blocks when PP-Structure output is present.

    The plain PP-OCR path returns only recognition polygons and carries no
    layout labels. In that case this returns an empty list: claiming block
    evidence that does not exist would let downstream stages drop text they
    should keep, so absence is reported honestly.
    """

    if not isinstance(raw, list) and not isinstance(raw, dict):
        return []
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if not isinstance(raw, dict):
        return []
    layout = raw.get("layout_det_res") or raw.get("layout")
    if not isinstance(layout, list):
        return []

    blocks: list[dict[str, Any]] = []
    for region in layout:
        if not isinstance(region, dict):
            continue
        label = canonical_block_label(region.get("label") or region.get("type"))
        raw_box = region.get("bbox") or region.get("box")
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            continue
        try:
            box = _normalized_box(raw_box, region)
        except (TypeError, ValueError):
            continue
        # PaddleOCR layout regions carry no line range; the block describes a
        # page area, so it deliberately covers no lines.
        blocks.append({"type": label, "box": box, "line_start": 0, "line_end": 0})
    return blocks


def _normalized_box(raw_box: list[Any], region: dict[str, Any]) -> list[float]:
    """Convert a layout box into normalized ``[x, y, width, height]``.

    PP-Structure reports boxes as pixel ``[x1, y1, x2, y2]`` corners, not as an
    origin plus size. Treating that pair as width/height silently shrinks or
    grows every region, so the corner semantics are explicit here.
    """

    x1, y1, x2, y2 = (float(value) for value in raw_box)
    width = region.get("width")
    height = region.get("height")
    if isinstance(width, (int, float)) and isinstance(height, (int, float)):
        if width <= 0 or height <= 0:
            raise ValueError("layout region has non-positive page dimensions")
        return clamp_box(
            [
                x1 / width,
                y1 / height,
                (x2 - x1) / width,
                (y2 - y1) / height,
            ]
        )
    # No page dimensions to scale by: accept an already-normalized corner pair.
    if all(0.0 <= value <= 1.0 for value in (x1, y1, x2, y2)):
        return clamp_box([x1, y1, x2 - x1, y2 - y1])
    raise TypeError("layout region is missing page dimensions")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--page-index", required=True, type=int)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="scribeforge-paddle-") as tmp:
        output_dir = Path(tmp)
        subprocess.run(
            [*build_command(args.image), "--save_path", str(output_dir)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        result_path = output_dir / f"{Path(args.image).stem}_res.json"
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    version = str(raw.get("version", "3.x")) if isinstance(raw, dict) else "3.x"
    sys.stdout.buffer.write(
        (
            json.dumps(
                result_document(
                    "paddleocr",
                    version,
                    args.page_index,
                    extract_lines(raw, image_size=_image_size(args.image)),
                    blocks=_layout_blocks(raw),
                    direction=reported_direction(_engine_direction(raw)),
                ),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    )
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
