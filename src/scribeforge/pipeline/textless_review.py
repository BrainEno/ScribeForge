"""Evidence-bound human review of a scanned page with no OCR lines."""

from __future__ import annotations

import base64
import io
import re
from pathlib import Path

from PIL import Image

from scribeforge.domain.page_asset import PageAssetError, verify_recorded_bytes
from scribeforge.storage.sqlite import PageConfirmation, PageRecord, SQLiteStore

MAX_RASTER_BYTES = 12 * 1024 * 1024
MAX_RASTER_PIXELS = 24_000_000
MAX_REASON = 1000
_TOKEN = re.compile(r"p[1-9][0-9]{0,18}:v[1-9][0-9]{0,18}:[a-f0-9]{64}\Z")
_HISTORY_LIMIT = 20


def _token(page: PageRecord, verification: int) -> str:
    return f"p{page.id}:v{verification}:{page.page_asset_sha256}"


def _inspect(store: SQLiteStore, project: int, page_index: int) -> tuple[PageRecord, int, bool]:
    if not 0 < project < 2**63 or not 0 <= page_index < 2**63:
        raise ValueError("invalid project or physical page index")
    verification = store.latest_verification_id(project, page_index)
    page = store.verification_page_record(project, verification)
    record = store.load_verification(verification)
    empty = (
        not record.alignment.pairs
        and not store.load_ocr_run(record.primary_run_id).lines
        and not store.load_ocr_run(record.secondary_run_id).lines
    )
    return page, verification, empty


def _verified_raster(page: PageRecord) -> bytes:
    evidence = page.asset_evidence()
    if evidence is None:
        raise PageAssetError("page has no saved raster digest; audit and rehash before confirming")
    if evidence.byte_size > MAX_RASTER_BYTES or page.width * page.height > MAX_RASTER_PIXELS:
        raise PageAssetError("page raster exceeds the desktop confirmation preview limit")
    with Path(page.source_image).open("rb") as handle:
        data = handle.read(MAX_RASTER_BYTES + 1)
    if len(data) > MAX_RASTER_BYTES:
        raise PageAssetError("page raster exceeds the desktop confirmation preview limit")
    verify_recorded_bytes(data, evidence, what=f"page {page.page_index}")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.size != (page.width, page.height) or getattr(image, "n_frames", 1) != 1:
                raise PageAssetError(
                    "page raster dimensions or frame count do not match saved evidence"
                )
            if image.format not in {"PNG", "JPEG"}:
                raise PageAssetError(
                    "only saved PNG and JPEG pages can be confirmed in the desktop"
                )
            image.verify()
    except (OSError, Image.DecompressionBombError) as exc:
        raise PageAssetError("saved page raster could not be decoded") from exc
    return data


def _confirmation_row(
    item: PageConfirmation, current_verification: int, page: PageRecord
) -> dict[str, object]:
    if item.verification_id is None:
        binding = "legacy"
    elif (
        item.verification_id == current_verification
        and item.raster_sha256 == page.page_asset_sha256
    ):
        binding = "current"
    else:
        binding = "historical"
    return {
        "id": item.id,
        "kind": item.kind,
        "actor": item.actor,
        "reason": item.reason[:MAX_REASON],
        "reason_truncated": len(item.reason) > MAX_REASON,
        "created_at": item.created_at,
        "verification_id": item.verification_id,
        "binding": binding,
    }


def textless_preview(store: SQLiteStore, project: int, page_index: int) -> dict[str, object]:
    page, verification, empty = _inspect(store, project, page_index)
    history = store.list_page_confirmations(project, page_index)
    rows = [
        _confirmation_row(item, verification, page) for item in reversed(history[-_HISTORY_LIMIT:])
    ]
    blocked = None
    if not empty:
        blocked = "the latest verification has aligned text or raw OCR lines"
    elif rows and rows[0]["binding"] in {"current", "legacy"}:
        blocked = "the page already has a confirmation"
    data = _verified_raster(page)
    if store.latest_verification_id(project, page_index) != verification:
        raise ValueError("verification changed while preparing preview; reload the page")
    store.require_current_page(project, page)
    return {
        "schema_version": 1,
        "project_id": project,
        "page_index": page_index,
        "verification_id": verification,
        "raster_sha256": page.page_asset_sha256,
        "width": page.width,
        "height": page.height,
        "raster_base64": base64.b64encode(data).decode("ascii"),
        "confirmation_token": _token(page, verification) if blocked is None else None,
        "blocked_reason": blocked,
        "history": rows,
        "history_truncated": len(history) > _HISTORY_LIMIT,
    }


def submit_textless_confirmation(
    store: SQLiteStore, project: int, page_index: int, *, token: str, kind: str, reason: str
) -> int:
    if _TOKEN.fullmatch(token) is None:
        raise ValueError("invalid textless confirmation token; reload the original page")
    if (
        kind not in {"blank", "non_text"}
        or not reason.strip()
        or "\x00" in reason
        or len(reason.strip()) > MAX_REASON
    ):
        raise ValueError(
            f"choose a textless page kind and enter a reason of 1–{MAX_REASON} characters"
        )
    page, verification, empty = _inspect(store, project, page_index)
    if not empty or token != _token(page, verification):
        raise ValueError("page verification or raster changed; reload before confirming")
    _verified_raster(page)
    return store.record_page_confirmation(
        project_id=project,
        page_index=page_index,
        kind=kind,
        actor="human",
        reason=reason.strip(),
        expected_page=page,
        expected_verification_id=verification,
    )
