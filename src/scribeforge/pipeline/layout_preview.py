"""Create an explicitly unverified preview of a reported original-frame region.

This is inspection aid only. It creates no project evidence, confirmation, or
exportable image; model boxes and reading order still require human review.
"""

from __future__ import annotations

import hashlib
import io
import math
import sqlite3
from contextlib import closing
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from PIL import Image

from scribeforge.domain.ocr import BlockType, BoundingBox
from scribeforge.storage.sqlite import SQLiteStore


def _pixel_box(box: BoundingBox, width: int, height: int) -> tuple[int, int, int, int]:
    values = (box.x, box.y, box.width, box.height)
    if not all(math.isfinite(value) for value in values) or box.width <= 0 or box.height <= 0:
        raise ValueError("region box must have finite, positive dimensions")
    if box.x < 0 or box.y < 0 or box.x + box.width > 1 or box.y + box.height > 1:
        raise ValueError("region box must fit inside the OCR frame")
    x, y, w, h = (Decimal(str(value)) for value in values)
    left = int((x * width).to_integral_value(rounding=ROUND_FLOOR))
    top = int((y * height).to_integral_value(rounding=ROUND_FLOOR))
    right = min(width, int(((x + w) * width).to_integral_value(rounding=ROUND_CEILING)))
    bottom = min(height, int(((y + h) * height).to_integral_value(rounding=ROUND_CEILING)))
    if left >= right or top >= bottom:
        raise ValueError("region box does not cover any pixels")
    return left, top, right, bottom


def create_layout_preview(
    database: Path,
    *,
    project_id: int,
    page_index: int,
    run_id: int,
    block_index: int,
    output: Path,
    context_output: Path | None = None,
) -> dict[str, object]:
    """Crop a latest-verification figure/table when its frame is the original.

    The original bytes must match *both* recorded import and OCR frame digests.
    Unknown, legacy and preprocessed frames cannot be mapped safely. All database
    reads are read-only, an observer rejects concurrent commits, and the output is
    created exclusively after validation, never replacing an existing file.
    """
    if min(project_id, run_id) < 1 or min(page_index, block_index) < 0:
        raise ValueError("invalid project, page, run or block index")
    if output.suffix.lower() != ".png":
        raise ValueError("preview output must be a .png file")
    if context_output is not None:
        if context_output.suffix.lower() != ".png":
            raise ValueError("context output must be a .png file")
        if context_output.resolve() == output.resolve():
            raise ValueError("preview and context outputs must be different files")
    if not database.is_file():
        raise ValueError("project database not found")
    store = SQLiteStore(database, read_only=True)
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as observer:
        before = observer.execute("PRAGMA data_version").fetchone()[0]
        pages = {page.page_index: page for page in store.list_pages(project_id)}
        if page_index not in pages:
            raise ValueError("physical page not found in project")
        page = pages[page_index]
        verification_id = store.latest_verification_id(project_id, page_index)
        verification = store.load_verification(verification_id)
        if run_id not in (verification.primary_run_id, verification.secondary_run_id):
            raise ValueError("run is not part of the page's latest verification")
        result = store.load_ocr_run(run_id)
        if block_index >= len(result.blocks):
            raise ValueError("block index not found in OCR run")
        block = result.blocks[block_index]
        if block.block_type not in (BlockType.FIGURE, BlockType.TABLE):
            raise ValueError("only figure and table observations can be previewed")
        frame = store.load_ocr_frame(run_id)
        if frame is None or frame.kind != "original":
            raise ValueError(
                "OCR frame is unknown or preprocessed; original coordinates are unsafe"
            )
        evidence = page.asset_evidence()
        if evidence is None:
            raise ValueError("page raster has no recorded import digest")
        data = Path(page.source_image).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != evidence.sha256 or len(data) != evidence.byte_size:
            raise ValueError("page raster differs from recorded import evidence")
        if digest != frame.sha256 or (page.width, page.height) != (frame.width, frame.height):
            raise ValueError("OCR frame does not match the recorded original page")
        with Image.open(io.BytesIO(data)) as image:
            if image.size != (page.width, page.height):
                raise ValueError("page raster dimensions differ from recorded evidence")
            rectangle = _pixel_box(block.box, *image.size)
            crop = image.crop(rectangle)
            encoded = io.BytesIO()
            crop.save(encoded, format="PNG")
            context_data = None
            context_size = None
            if context_output is not None:
                # Both images come from the exact same checked bytes. The overview
                # is reduced for display; no annotations are baked into evidence.
                overview = image.copy()
                overview.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                context_size = list(overview.size)
                context_encoded = io.BytesIO()
                overview.save(context_encoded, format="PNG")
                context_data = context_encoded.getvalue()
        if before != observer.execute("PRAGMA data_version").fetchone()[0]:
            raise ValueError("project changed during preview; try again")
    preview = encoded.getvalue()
    # Exclusive creation prevents an output path from replacing evidence or an
    # earlier preview. No directories are created implicitly inside the project.
    created: list[Path] = []
    try:
        for target, payload in ((output, preview), (context_output, context_data)):
            if target is None or payload is None:
                continue
            stream = target.open("xb")
            created.append(target)
            with stream:
                written = stream.write(payload)
                if written != len(payload):
                    raise OSError("incomplete preview write")
    except BaseException:
        for target in reversed(created):
            target.unlink(missing_ok=True)
        raise
    result_json: dict[str, object] = {
        "schema_version": 1,
        "status": "unverified_preview",
        "project_id": project_id,
        "page_index": page_index,
        "verification_id": verification_id,
        "run_id": run_id,
        "engine": result.engine,
        "block_index": block_index,
        "block_type": block.block_type.value,
        "source_sha256": digest,
        "frame_kind": frame.kind,
        "frame_size": [frame.width, frame.height],
        "normalized_box": [block.box.x, block.box.y, block.box.width, block.box.height],
        "pixel_box": list(rectangle),
        "preview_sha256": hashlib.sha256(preview).hexdigest(),
        "output": str(output),
        "notice": "Unverified OCR observation; not human confirmation or an exportable figure.",
    }
    if context_output is not None and context_data is not None:
        result_json["context"] = {
            "output": str(context_output),
            "sha256": hashlib.sha256(context_data).hexdigest(),
            "size": context_size,
        }
    return result_json
