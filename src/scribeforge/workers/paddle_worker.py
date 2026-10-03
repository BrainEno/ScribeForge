from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scribeforge.workers.protocol import normalize_polygon, result_document


def build_command(image: str) -> list[str]:
    return ["paddleocr", "ocr", "--input", image, "--model_name", "PP-OCRv6"]


def extract_lines(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if not isinstance(raw, dict):
        raise TypeError("unexpected PaddleOCR JSON output")

    width_value: Any = raw.get("width", raw.get("image_width", 1))
    height_value: Any = raw.get("height", raw.get("image_height", 1))
    width = float(1 if width_value is None else width_value)
    height = float(1 if height_value is None else height_value)

    texts: Any = raw.get("rec_texts", [])
    scores: Any = raw.get("rec_scores", [])
    polygons: Any = raw.get("rec_polys", raw.get("dt_polys", []))
    if not isinstance(texts, list) or not isinstance(scores, list) or not isinstance(polygons, list):
        raise TypeError("PaddleOCR result arrays must be lists")
    if not (len(texts) == len(scores) == len(polygons)):
        raise ValueError("PaddleOCR result arrays have inconsistent lengths")

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
        output = Path(tmp) / "result.json"
        subprocess.run(
            [*build_command(args.image), "--save_path", str(output)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        raw = json.loads(output.read_text(encoding="utf-8"))
    version = str(raw.get("version", "3.x")) if isinstance(raw, dict) else "3.x"
    print(
        json.dumps(
            result_document("paddleocr", version, args.page_index, extract_lines(raw)),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
