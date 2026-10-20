from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scribeforge.workers.protocol import normalize_polygon, result_document


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
                ),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    )
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
