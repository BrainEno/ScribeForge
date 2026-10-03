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
        raise ValueError("unexpected PaddleOCR JSON output")
    width = float(raw.get("width", raw.get("image_width", 1)))
    height = float(raw.get("height", raw.get("image_height", 1)))
    texts = raw.get("rec_texts", [])
    scores = raw.get("rec_scores", [])
    polygons = raw.get("rec_polys", raw.get("dt_polys", []))
    if not (len(texts) == len(scores) == len(polygons)):
        raise ValueError("PaddleOCR result arrays have inconsistent lengths")
    lines = []
    for text, score, polygon in zip(texts, scores, polygons, strict=True):
        box = normalize_polygon(polygon, width, height)
        lines.append({"text": str(text), "box": box, "tokens": [{"text": str(text), "confidence": float(score), "box": box}]})
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--page-index", required=True, type=int)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="scribeforge-paddle-") as tmp:
        output = Path(tmp) / "result.json"
        subprocess.run([*build_command(args.image), "--save_path", str(output)], check=True, capture_output=True, text=True, encoding="utf-8")
        raw = json.loads(output.read_text(encoding="utf-8"))
    version = str(raw.get("version", "3.x")) if isinstance(raw, dict) else "3.x"
    print(json.dumps(result_document("paddleocr", version, args.page_index, extract_lines(raw)), ensure_ascii=False))


if __name__ == "__main__":
    main()
