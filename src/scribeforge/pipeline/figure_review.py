"""Bind a reviewed crop to exportable inline-figure evidence (issue #246).

A model box is never enough. The reviewer first creates a preview with
``layout-preview``, compares it to the original page, then submits that exact
crop with a confirmation token. This module re-derives the crop from the current
source bytes, refuses stale verifications or changed rasters, and only then
records the confirmation used by EPUB assembly.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image

from scribeforge.domain.ocr import BlockType, BoundingBox
from scribeforge.pipeline.layout_preview import _pixel_box, parse_figure_confirmation_token
from scribeforge.storage.sqlite import FigureConfirmation, SQLiteStore

MAX_CROP_BYTES = 33_554_432


def render_region_crop(
    data: bytes, box: tuple[float, float, float, float]
) -> tuple[bytes, tuple[int, int, int, int]]:
    """Crop the recorded normalized box from original page bytes as PNG."""
    with Image.open(io.BytesIO(data)) as image:
        rectangle = _pixel_box(BoundingBox(*box), *image.size)
        encoded = io.BytesIO()
        image.crop(rectangle).save(encoded, format="PNG")
        return encoded.getvalue(), rectangle


def submit_figure_confirmation(
    database: Path,
    *,
    project_id: int,
    page_index: int,
    run_id: int,
    block_index: int,
    crop_path: Path,
    token: str,
    anchor_pair_index: int,
    caption: str = "",
    reason: str = "",
) -> FigureConfirmation:
    """Re-check the reviewed crop against live evidence and persist it.

    The crop file must be byte-identical to a fresh crop of the current original
    page for the boxed OCR observation. Any verification, raster, frame or box
    change rejects the confirmation; nothing is written on failure.
    """
    if min(project_id, run_id) < 1 or min(page_index, block_index, anchor_pair_index) < 0:
        raise ValueError("invalid figure confirmation scope")
    reason = reason.strip()
    caption = caption.strip()
    if not reason or len(reason) > 1000:
        raise ValueError("enter a reason of 1–1000 characters")
    if len(caption) > 1000:
        raise ValueError("caption must be at most 1000 characters")
    parsed = parse_figure_confirmation_token(token)
    if int(str(parsed["run_id"])) != run_id or int(str(parsed["block_index"])) != block_index:
        raise ValueError("confirmation token does not match the selected observation")
    box = parsed["box"]
    assert isinstance(box, tuple)
    store = SQLiteStore(database)
    pages = {page.page_index: page for page in store.list_pages(project_id)}
    if page_index not in pages:
        raise ValueError("physical page not found in project")
    page = pages[page_index]
    verification_id = store.latest_verification_id(project_id, page_index)
    if verification_id != int(str(parsed["verification_id"])):
        raise ValueError("verification changed; create a new preview before confirming")
    verification = store.load_verification(verification_id)
    if run_id not in (verification.primary_run_id, verification.secondary_run_id):
        raise ValueError("run is not part of the page's latest verification")
    result = store.load_ocr_run(run_id)
    if block_index >= len(result.blocks):
        raise ValueError("block index not found in OCR run")
    block = result.blocks[block_index]
    if block.block_type not in (BlockType.FIGURE, BlockType.TABLE):
        raise ValueError("only figure/table observations can be confirmed")
    actual_box = (block.box.x, block.box.y, block.box.width, block.box.height)
    if actual_box != box:
        raise ValueError("confirmation token box does not match the OCR block")
    frame = store.load_ocr_frame(run_id)
    evidence = page.asset_evidence()
    if evidence is None:
        raise ValueError("page raster has no recorded import digest")
    if frame is None or frame.kind != "original" or frame.sha256 != evidence.sha256:
        raise ValueError("OCR frame is not the confirmed original page")
    data = Path(page.source_image).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != evidence.sha256 or len(data) != evidence.byte_size:
        raise ValueError("page raster differs from recorded import evidence")
    if digest != parsed["source_sha256"]:
        raise ValueError("confirmation token source digest is stale")
    crop, _ = render_region_crop(data, box)
    crop_digest = hashlib.sha256(crop).hexdigest()
    if crop_digest != parsed["crop_sha256"]:
        raise ValueError("fresh crop differs from the reviewed preview")
    if not crop_path.is_file():
        raise ValueError("reviewed crop file not found")
    reviewed = crop_path.read_bytes()
    if len(reviewed) > MAX_CROP_BYTES:
        raise ValueError("reviewed crop exceeds the 32 MiB limit")
    if reviewed != crop or hashlib.sha256(reviewed).hexdigest() != crop_digest:
        raise ValueError("reviewed crop file is not the previewed original pixels")
    if anchor_pair_index > len(verification.alignment.pairs):
        raise ValueError("reading anchor is past the page's aligned pairs")
    confirmation_id = store.record_figure_confirmation(
        project_id=project_id,
        page_index=page_index,
        verification_id=verification_id,
        run_id=run_id,
        block_index=block_index,
        box=box,
        source_sha256=digest,
        crop_png=crop,
        anchor_pair_index=anchor_pair_index,
        caption=caption,
        reason=reason,
        expected_page=page,
    )
    stored = next(
        item
        for item in store.list_figure_confirmations(project_id, page_index)
        if item.id == confirmation_id
    )
    return stored


def latest_inline_confirmations(
    store: SQLiteStore, project_id: int
) -> dict[int, tuple[FigureConfirmation, ...]]:
    """Newest-batch confirmations per page, ordered by id within the page."""
    grouped: dict[int, tuple[FigureConfirmation, ...]] = {}
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            continue
        # History may contain re-confirmations of the same observation; only the
        # newest row per OCR run/block is the live decision.
        latest: dict[tuple[int, int], FigureConfirmation] = {}
        for item in store.list_figure_confirmations(project_id, page.page_index):
            if item.verification_id != verification_id:
                continue
            key = (item.run_id, item.block_index)
            if key not in latest or item.id > latest[key].id:
                latest[key] = item
        if latest:
            grouped[page.page_index] = tuple(sorted(latest.values(), key=lambda item: item.id))
    return grouped
