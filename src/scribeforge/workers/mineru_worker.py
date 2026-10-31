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
    reported_direction,
    result_document,
)

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
    """Clamp a normalized MinerU block bbox into a constructible page box.

    MinerU emits block boxes in normalized page coordinates, but a block flush
    against the trim (or a page whose margins the scanner cropped) can come back
    slightly outside 0..1. Clamping keeps that evidence usable instead of
    failing the page on an otherwise valid detection.
    """

    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError("MinerU block is missing a normalized bbox")
    x1, y1, x2, y2 = (min(max(float(value), 0.0), 1.0) for value in raw)
    return [x1, y1, max(x2 - x1, 0.0), max(y2 - y1, 0.0)]


def _page_blocks(raw: Any) -> list[Any]:
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
    return blocks


def _item_box(item: dict[str, Any], fallback: list[float]) -> list[float]:
    """Prefer a content item's own bbox, else fall back to the block bbox.

    MinerU 4 reports a paragraph bbox for the block; some tiers also attach a
    bbox to each content item. Using the item bbox when it exists gives real
    line-level geometry, which alignment and review crops depend on.
    """

    raw = item.get("bbox")
    if isinstance(raw, list) and len(raw) == 4:
        return _box(raw)
    return fallback


def extract_page(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Flatten MinerU middle-JSON into the contract v2 lines and blocks.

    MinerU groups visual lines into blocks and labels each block. Those labels
    (title, footnote, header, footer, figure, table) are the evidence the
    downstream layers need to drop page furniture, recognise chapter headings
    and keep figures out of the text layer, so they are preserved here instead
    of being flattened away.
    """

    lines: list[dict[str, Any]] = []
    reported_blocks: list[dict[str, Any]] = []
    for block in _page_blocks(raw):
        if not isinstance(block, dict):
            raise TypeError("MinerU block must be an object")
        content = block.get("content")
        if content is None:
            continue
        if not isinstance(content, list):
            raise TypeError("MinerU block content must be a list")

        block_box = _box(block.get("bbox"))
        block_label = canonical_block_label(block.get("type"))
        start = len(lines)
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("content")
            if not isinstance(text, str):
                raise TypeError("MinerU text content must be a string")
            entry: dict[str, Any] = {
                "text": text,
                "box": _item_box(item, block_box),
                "tokens": [],
            }
            score = item.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                entry["confidence"] = min(max(float(score), 0.0), 1.0)
            lines.append(entry)
        if len(lines) > start:
            reported_blocks.append(
                block_document(block_label, block_box, start, len(lines))
            )
    return lines, reported_blocks


def extract_lines(raw: Any) -> list[dict[str, Any]]:
    """Flatten MinerU middle-JSON into the worker line contract only."""

    lines, _blocks = extract_page(raw)
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
        # The adapter captures this worker's stderr into a bounded, disk-backed
        # log. Inherit that stream so MinerU diagnostics stay bounded instead of
        # accumulating unbounded ``capture_output`` bytes in worker memory.
        subprocess.run(
            build_command(args.image, output_dir),
            check=True,
            stdout=sys.stderr,
            stderr=sys.stderr,
        )
        raw = json.loads(_result_path(output_dir, args.image).read_text(encoding="utf-8"))
    lines, blocks = extract_page(raw)
    sys.stdout.buffer.write(
        (
            json.dumps(
                result_document(
                    "mineru",
                    _version(raw),
                    args.page_index,
                    lines,
                    blocks=blocks,
                    direction=reported_direction(
                        raw.get("direction") if isinstance(raw, dict) else None
                    ),
                ),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    )
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
