"""Resolve persisted OCR coordinate frames without guessing legacy provenance."""

from pathlib import Path

from PIL import Image

from scribeforge.domain.page_asset import PageAssetError, file_sha256
from scribeforge.storage.sqlite import OCRInputFrame, PageRecord, SQLiteStore


def capture_frame(path: Path, kind: str, width: int, height: int) -> OCRInputFrame:
    with Image.open(path) as image:
        if image.size != (width, height):
            raise PageAssetError("OCR frame dimensions do not match the recorded raster")
    return OCRInputFrame(kind, file_sha256(path), width, height)


def verification_frame(
    store: SQLiteStore, project_id: int, page: PageRecord, verification_id: int
) -> tuple[OCRInputFrame, Path]:
    record = store.load_verification(verification_id)
    first = store.load_ocr_frame(record.primary_run_id)
    second = store.load_ocr_frame(record.secondary_run_id)
    if first is None or second is None:
        raise ValueError("OCR frame is unknown for legacy evidence; re-import into a new project")
    if first != second:
        raise ValueError("OCR frame mismatch between the verification runs")
    if first.kind == "original":
        path = Path(page.source_image)
    elif first.kind == "preprocessed":
        preprocessed = store.load_preprocess(project_id, page.page_index)
        if preprocessed is None or preprocessed.output_sha256 != first.sha256:
            raise ValueError("OCR frame does not match recorded preprocessing evidence")
        path = Path(preprocessed.output_image)
    else:
        raise ValueError("unknown OCR frame kind")
    try:
        current = capture_frame(path, first.kind, first.width, first.height)
    except OSError as exc:
        raise PageAssetError(f"OCR frame is missing or unreadable: {exc}") from exc
    if current != first:
        raise PageAssetError("OCR frame digest mismatch; restore it from a verified backup")
    return first, path
