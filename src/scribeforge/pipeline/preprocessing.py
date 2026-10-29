"""The PREPROCESS pipeline stage.

Sits between import and OCR: it takes the untouched page raster recorded at
import, writes a corrected copy, and persists the parameters that produced it.

Two invariants drive the design:

1. **The original raster is evidence and is never modified.** Preprocessing is
   lossy by nature (rotation resampling, denoising); keeping the original means
   a bad setting can always be revisited without re-importing the book.
2. **Normalized OCR coordinates are relative to the preprocessed frame.** Any
   transform that changes geometry (deskew, crop) shifts where a box lands, so
   the frame that produced the boxes must also produce the review crops.
   Otherwise reviewers are shown the wrong pixels — worse than showing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image

from scribeforge.adapters.image_preprocess import apply_preprocess
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.preprocess import (
    PreprocessOperation,
    PreprocessOutcome,
    PreprocessSettings,
)
from scribeforge.storage.sqlite import PageRecord, PreprocessRecord, SQLiteStore


@dataclass(frozen=True, slots=True)
class PreprocessPageResult:
    """The raster the OCR engines should read, plus its evidence."""

    image_path: Path
    width: int
    height: int
    outcome: PreprocessOutcome
    was_preprocessed: bool


def preprocessed_asset_name(page_index: int) -> str:
    return f"page-{page_index:05d}-pre.png"


def _write_atomic(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _dimensions(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as image:
        return image.size


def _reuse(stored: PreprocessRecord, settings: PreprocessSettings) -> PreprocessPageResult:
    return PreprocessPageResult(
        image_path=Path(stored.output_image),
        width=stored.output_width,
        height=stored.output_height,
        outcome=PreprocessOutcome(
            page_index=stored.page_index,
            settings=settings,
            applied=tuple(PreprocessOperation(name) for name in stored.applied),
            measured_skew_degrees=stored.measured_skew_degrees,
            output_sha256=stored.output_sha256,
            notes=stored.notes,
        ),
        was_preprocessed=bool(stored.applied),
    )


def preprocess_page(
    store: SQLiteStore,
    project_id: int,
    page: PageRecord,
    settings: PreprocessSettings,
    *,
    assets_dir: Path,
) -> PreprocessPageResult:
    """Run the PREPROCESS stage for one page, resumably.

    Re-running is safe and idempotent: an existing raster is reused rather than
    re-encoded, because re-encoding a rotated raster rotates it a second time,
    which is silently destructive.
    """

    stored = store.load_preprocess(project_id, page.page_index)
    job_id = store.ensure_job(
        project_id, JobStage.PREPROCESS, page_index=page.page_index
    )
    state = store.load_job(job_id).state

    if stored is not None and Path(stored.output_image).is_file():
        if state is not JobState.SUCCEEDED:
            store.start_job(job_id)
            store.complete_job(job_id)
        return _reuse(stored, settings)

    if state is JobState.SUCCEEDED:
        # The stage claims success but the raster is gone (deleted assets,
        # restored partial backup). Say so and rebuild instead of pretending.
        store.reopen_job(job_id, "preprocessed raster is missing from disk")

    store.start_job(job_id)
    try:
        data, outcome = apply_preprocess(
            Path(page.source_image).read_bytes(), settings, page_index=page.page_index
        )
        destination = assets_dir / preprocessed_asset_name(page.page_index)
        _write_atomic(destination, data)
        width, height = _dimensions(data)
        store.record_preprocess(
            project_id,
            page.page_index,
            settings=outcome.settings.fingerprint(),
            applied=tuple(operation.value for operation in outcome.applied),
            measured_skew_degrees=outcome.measured_skew_degrees,
            output_image=str(destination),
            output_sha256=outcome.output_sha256,
            output_width=width,
            output_height=height,
            notes=outcome.notes,
        )
    except Exception as exc:
        store.fail_job(job_id, str(exc).strip() or type(exc).__name__)
        raise
    store.complete_job(job_id)
    return PreprocessPageResult(
        image_path=destination,
        width=width,
        height=height,
        outcome=outcome,
        was_preprocessed=bool(outcome.applied),
    )
