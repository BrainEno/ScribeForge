from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from scribeforge.workers.protocol import normalize_polygon, result_document


def build_command(image: str) -> list[str]:
    return ["mineru-kit", "parse", image, "--json"]


def extract_lines(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        raise TypeError("unexpected MinerU JSON output")
    page = raw.get("page") or raw
    width = float(page.get("width", 1))
    height = float(page.get("height", 1))
    lines = []
    for item in page.get("lines", []):
        polygon = item.get("polygon") or item.get("poly")
        box = item.get("box") or item.get("bbox")
        if polygon:
            normalized = normalize_polygon(polygon, width, height)
        elif isinstance(box, list) and len(box) == 4:
            x1, y1, x2, y2 = (float(value) for value in box)
            normalized = [x1 / width, y1 / height, (x2 - x1) / width, (y2 - y1) / height]
        else:
            raise ValueError("MinerU line is missing geometry")
        lines.append({"text": str(item["text"]), "box": normalized, "tokens": []})
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--page-index", required=True, type=int)
    args = parser.parse_args()
    completed = subprocess.run(build_command(args.image), check=True, capture_output=True, text=True, encoding="utf-8")
    raw = json.loads(completed.stdout)
    version = str(raw.get("version", "4.x")) if isinstance(raw, dict) else "4.x"
    print(json.dumps(result_document("mineru", version, args.page_index, extract_lines(raw)), ensure_ascii=False))


if __name__ == "__main__":
    main()
