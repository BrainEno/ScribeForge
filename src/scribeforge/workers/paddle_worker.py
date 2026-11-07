from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scribeforge.workers.paddle_model_setup import PADDLE_MODELS, verify_paddle_model
from scribeforge.workers.protocol import (
    canonical_block_label,
    clamp_box,
    normalize_polygon,
    reported_direction,
    result_document,
    run_engine_command,
)


def locked_model_directories() -> dict[str, Path]:
    """Verify managed model content before every real worker invocation."""

    raw_root = os.environ.get("SCRIBEFORGE_PADDLE_MODELS_ROOT")
    if raw_root is None or not raw_root.strip():
        return {}
    root = Path(raw_root)
    paths: dict[str, Path] = {}
    for asset in PADDLE_MODELS:
        path = root / asset.name
        verify_paddle_model(asset, path)
        paths[asset.name] = path
    return paths


def build_command(
    image: str, *, model_directories: Mapping[str, Path] | None = None
) -> list[str]:
    command = [
        "paddleocr",
        "ocr",
        "--input",
        image,
        "--ocr_version",
        # PP-OCRv5/v6's current Transformers recognizers do not implement
        # PaddleX return_word_box; PP-OCRv4's CTC path does.
        "PP-OCRv4",
        "--use_doc_orientation_classify",
        "False",
        "--use_doc_unwarping",
        "False",
        "--use_textline_orientation",
        "False",
        "--return_word_box",
        "true",
    ]
    if model_directories:
        detection = model_directories.get("PP-OCRv4_mobile_det")
        recognition = model_directories.get("PP-OCRv4_mobile_rec")
        if detection is not None:
            command.extend(
                [
                    "--text_detection_model_name",
                    "PP-OCRv4_mobile_det",
                    "--text_detection_model_dir",
                    str(detection),
                ]
            )
        if recognition is not None:
            command.extend(
                [
                    "--text_recognition_model_name",
                    "PP-OCRv4_mobile_rec",
                    "--text_recognition_model_dir",
                    str(recognition),
                ]
            )
    return command


def _image_size(path: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def _normalize_word_box(raw_box: Any, width: float, height: float) -> list[float]:
    """Normalize PaddleOCR's native word box ``[left, top, right, bottom]``."""

    if not isinstance(raw_box, list) or len(raw_box) != 4:
        raise TypeError("PaddleOCR word box must contain left, top, right, bottom")
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool)
        for value in raw_box
    ):
        raise TypeError("PaddleOCR word box coordinates must be numeric")
    left, top, right, bottom = (float(value) for value in raw_box)
    if right < left or bottom < top:
        raise ValueError("PaddleOCR word box has reversed coordinates")
    return normalize_polygon(
        [[left, top], [right, top], [right, bottom], [left, bottom]],
        width,
        height,
    )


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

    raw_word_text_groups = raw.get("text_word")
    raw_word_box_groups = raw.get("text_word_boxes")
    word_text_groups: list[Any] | None
    word_box_groups: list[Any] | None
    if raw_word_text_groups is None and raw_word_box_groups is None:
        # Older Paddle output and engines without native word boxes do not
        # provide token geometry. Never relabel the line polygon as a token.
        word_text_groups = None
        word_box_groups = None
    else:
        if not isinstance(raw_word_text_groups, list) or not isinstance(
            raw_word_box_groups, list
        ):
            raise TypeError("PaddleOCR word outputs must be lists")
        if not (
            len(raw_word_text_groups)
            == len(raw_word_box_groups)
            == len(texts)
        ):
            raise ValueError("PaddleOCR word arrays have inconsistent lengths")
        word_text_groups = raw_word_text_groups
        word_box_groups = raw_word_box_groups

    lines: list[dict[str, Any]] = []
    for line_index, (text, score, polygon) in enumerate(
        zip(texts, scores, polygons, strict=True)
    ):
        if not isinstance(polygon, list):
            raise TypeError("PaddleOCR polygon must be a list")
        box = normalize_polygon(polygon, width, height)
        tokens: list[dict[str, Any]] | None = None
        if word_text_groups is not None and word_box_groups is not None:
            raw_word_texts = word_text_groups[line_index]
            raw_word_boxes = word_box_groups[line_index]
            if not isinstance(raw_word_texts, list) or not isinstance(raw_word_boxes, list):
                raise TypeError("PaddleOCR per-line word outputs must be lists")
            if len(raw_word_texts) != len(raw_word_boxes):
                raise ValueError("PaddleOCR per-line word arrays have inconsistent lengths")
            tokens = []
            for word_text, word_box in zip(raw_word_texts, raw_word_boxes, strict=True):
                if not isinstance(word_text, str):
                    raise TypeError("PaddleOCR word text must be a string")
                tokens.append(
                    {
                        "text": word_text,
                        # PaddleOCR exposes a line-level rec_score, not an
                        # independently calibrated score for each word.
                        "box": _normalize_word_box(word_box, width, height),
                    }
                )
        lines.append(
            {
                "text": str(text),
                "box": box,
                "confidence": float(score),
                "tokens": tokens,
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


def _layout_blocks(
    raw: Any, lines: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Associate PP-Structure regions with OCR lines only when ownership is clear.

    Plain PP-OCR has no layout labels and returns no blocks.  When layout exists,
    a line is assigned only if its centre is inside a region, at least 60% of its
    box is covered, and exactly one region satisfies both tests.  Overlapping
    candidates, partial ownership and unassigned lines stay unknown: an empty
    ``0:0`` range cannot represent that distinction downstream.  Non-contiguous
    assignments become separate blocks rather than swallowing another region's
    lines into one fabricated range.
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
    if lines is None:
        try:
            lines = extract_lines(raw)
        except (TypeError, ValueError):
            return []
    if not lines:
        return []

    regions: list[tuple[str, list[float]]] = []
    for region in layout:
        if not isinstance(region, dict):
            continue
        label = canonical_block_label(region.get("label") or region.get("type"))
        raw_box = region.get("bbox") or region.get("box")
        if not isinstance(raw_box, list) or len(raw_box) != 4:
            continue
        # PP-Structure commonly puts image dimensions on the result document,
        # not on each layout region.  Copy them only for coordinate conversion;
        # the worker's source JSON is never mutated.
        conversion_region = region
        if not (
            isinstance(region.get("width"), (int, float))
            and isinstance(region.get("height"), (int, float))
        ):
            width = raw.get("width", raw.get("image_width"))
            height = raw.get("height", raw.get("image_height"))
            if isinstance(width, (int, float)) and isinstance(height, (int, float)):
                conversion_region = {**region, "width": width, "height": height}
        try:
            box = _normalized_box(raw_box, conversion_region)
        except (TypeError, ValueError):
            continue
        regions.append((label, box))
    if not regions:
        return []

    assigned: list[list[int]] = [[] for _ in regions]
    for line_index, line in enumerate(lines):
        raw_line_box = line.get("box")
        if not isinstance(raw_line_box, list) or len(raw_line_box) != 4:
            continue
        try:
            x, y, width, height = (float(value) for value in raw_line_box)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        centre_x = x + width / 2
        centre_y = y + height / 2
        line_area = width * height
        candidates: list[int] = []
        for region_index, (_label, box) in enumerate(regions):
            region_x, region_y, region_width, region_height = box
            inside = (
                region_x <= centre_x <= region_x + region_width
                and region_y <= centre_y <= region_y + region_height
            )
            if not inside:
                continue
            intersection = max(
                0.0, min(x + width, region_x + region_width) - max(x, region_x)
            ) * max(
                0.0, min(y + height, region_y + region_height) - max(y, region_y)
            )
            if intersection / line_area >= 0.60:
                candidates.append(region_index)
        if len(candidates) == 1:
            assigned[candidates[0]].append(line_index)

    blocks: list[dict[str, Any]] = []
    for (label, box), line_indexes in zip(regions, assigned, strict=True):
        if not line_indexes:
            continue
        run_start = previous = line_indexes[0]
        for line_index in line_indexes[1:]:
            if line_index != previous + 1:
                blocks.append(
                    {
                        "type": label,
                        "box": box,
                        "line_start": run_start,
                        "line_end": previous + 1,
                    }
                )
                run_start = line_index
            previous = line_index
        blocks.append(
            {"type": label, "box": box, "line_start": run_start, "line_end": previous + 1}
        )
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
    model_directories = locked_model_directories()
    with TemporaryDirectory(prefix="scribeforge-paddle-") as tmp:
        output_dir = Path(tmp)
        run_engine_command(
            (
                *build_command(args.image, model_directories=model_directories),
                "--save_path",
                str(output_dir),
            )
        )
        result_path = output_dir / f"{Path(args.image).stem}_res.json"
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    version = str(raw.get("version", "3.x")) if isinstance(raw, dict) else "3.x"
    lines = extract_lines(raw, image_size=_image_size(args.image))
    sys.stdout.buffer.write(
        (
            json.dumps(
                result_document(
                    "paddleocr",
                    version,
                    args.page_index,
                    lines,
                    blocks=_layout_blocks(raw, lines),
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
