from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scribeforge.workers.protocol import result_document

PARSE_TIER = "basic"


def build_command(image: str, output_dir: Path) -> list[str]:
    return [
        "mineru-kit",
        "parse",
        image,
        "-o",
        str(output_dir),
        "-f",
        "middle_json",
        "--tier",
        PARSE_TIER,
    ]


def _box(raw: Any) -> list[float]:
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError("MinerU block is missing a normalized bbox")
    x1, y1, x2, y2 = (float(value) for value in raw)
    return [x1, y1, x2 - x1, y2 - y1]


def extract_lines(raw: Any) -> list[dict[str, Any]]:
    """Flatten MinerU middle-JSON blocks into the worker line contract.

    MinerU 4 groups visual lines into paragraph blocks, so the block bbox is the
    only geometry it exposes. The text is kept verbatim and in reading order.
    """

    if not isinstance(raw, dict):
        raise TypeError("unexpected MinerU JSON output")
    pages = raw.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("MinerU output has no pages")
    page = pages[0]
    if not isinstance(page, dict):
        raise TypeError("MinerU page must be an object")
    blocks = page.get("blocks")
    if not isinstance(blocks, list):
        raise TypeError("MinerU page blocks must be a list")

    lines: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise TypeError("MinerU block must be an object")
        content = block.get("content")
        if content is None:
            continue
        if not isinstance(content, list):
            raise TypeError("MinerU block content must be a list")
        box = _box(block.get("bbox"))
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("content")
            if not isinstance(text, str):
                raise TypeError("MinerU text content must be a string")
            lines.append({"text": text, "box": box, "tokens": []})
    return lines


def _version(raw: Any) -> str:
    if isinstance(raw, dict):
        metadata = raw.get("metadata")
        if isinstance(metadata, dict):
            producer = metadata.get("producer")
            if isinstance(producer, dict):
                version = producer.get("version")
                if isinstance(version, str) and version.strip():
                    return version
    return "4.x"


def _result_path(output_dir: Path, image: str) -> Path:
    expected = output_dir / f"{Path(image).stem}.json"
    if expected.is_file():
        return expected
    candidates = sorted(output_dir.glob("*.json"))
    if len(candidates) != 1:
        raise FileNotFoundError(f"unexpected MinerU output: {output_dir}")
    return candidates[0]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--page-index", required=True, type=int)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="scribeforge-mineru-") as tmp:
        output_dir = Path(tmp)
        subprocess.run(
            build_command(args.image, output_dir),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        raw = json.loads(_result_path(output_dir, args.image).read_text(encoding="utf-8"))
    sys.stdout.buffer.write(
        (
            json.dumps(
                result_document("mineru", _version(raw), args.page_index, extract_lines(raw)),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    )
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
