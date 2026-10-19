from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from scribeforge.adapters.image_crops import materialize_review_crops
from scribeforge.domain.alignment import align_pages
from scribeforge.domain.crops import plan_review_crops
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import OCREngine
from scribeforge.domain.risk import assess_page, review_candidates
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.pipeline.vlm_review import review_pending_candidates
from scribeforge.storage.sqlite import PageRecord, SQLiteStore
from scribeforge.storage.vlm_state import SQLiteVLMReviewState

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class PageOutcome:
    page_index: int
    primary_run_id: int
    secondary_run_id: int
    verification_id: int
    vlm_review_count: int


@dataclass(frozen=True, slots=True)
class BookOutcome:
    project_id: int
    pages: tuple[PageOutcome, ...]
    failed_pages: tuple[tuple[int, str], ...]


def _run_stage(
    store: SQLiteStore,
    project_id: int,
    stage: JobStage,
    page_index: int,
    compute: Callable[[], None],
    resolve: Callable[[], T],
) -> T:
    """Drive one page-scoped stage, skipping it when it already succeeded.

    A stage persists its evidence before returning; on failure it records the
    error so the stage stays resumable and does not invalidate the whole book.
    """

    job_id = store.ensure_job(project_id, stage, page_index=page_index)
    if store.load_job(job_id).state is not JobState.SUCCEEDED:
        store.start_job(job_id)
        try:
            compute()
        except Exception as exc:
            store.fail_job(job_id, str(exc).strip() or type(exc).__name__)
            raise
        store.complete_job(job_id)
    return resolve()


def process_page(
    store: SQLiteStore,
    project_id: int,
    page: PageRecord,
    *,
    primary_engine: OCREngine,
    secondary_engine: OCREngine,
    reviewer: VLMReviewer,
    crops_dir: Path,
) -> PageOutcome:
    """Run every remaining pipeline stage for one page, resumably and page-granular."""

    page_index = page.page_index
    image_path = page.source_image

    def compute_primary() -> None:
        store.record_ocr(
            project_id, primary_engine.analyze_page(image_path, page_index)
        )

    primary_run_id = _run_stage(
        store,
        project_id,
        JobStage.PRIMARY_OCR,
        page_index,
        compute_primary,
        lambda: store.latest_engine_run(project_id, page_index, primary_engine.name),
    )

    def compute_secondary() -> None:
        store.record_ocr(
            project_id, secondary_engine.analyze_page(image_path, page_index)
        )

    secondary_run_id = _run_stage(
        store,
        project_id,
        JobStage.SECONDARY_OCR,
        page_index,
        compute_secondary,
        lambda: store.latest_engine_run(project_id, page_index, secondary_engine.name),
    )

    def compute_verify() -> None:
        primary = store.load_ocr_run(primary_run_id)
        secondary = store.load_ocr_run(secondary_run_id)
        alignment = align_pages(primary, secondary)
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        store.record_verification(
            primary_run_id, secondary_run_id, alignment, risks, candidates
        )

    verification_id = _run_stage(
        store,
        project_id,
        JobStage.VERIFY,
        page_index,
        compute_verify,
        lambda: store.latest_verification_id(project_id, page_index),
    )

    def compute_vlm() -> None:
        record = store.load_verification(verification_id)
        if not record.candidates:
            return
        plans = plan_review_crops(record.candidates, page.width, page.height)
        crop_paths = materialize_review_crops(Path(image_path), plans, crops_dir)
        state = SQLiteVLMReviewState(store, verification_id)
        review_pending_candidates(record.candidates, crop_paths, reviewer, state)

    vlm_review_count = _run_stage(
        store,
        project_id,
        JobStage.VLM_REVIEW,
        page_index,
        compute_vlm,
        lambda: len(store.reviewed_pair_indexes(verification_id)),
    )

    return PageOutcome(
        page_index=page_index,
        primary_run_id=primary_run_id,
        secondary_run_id=secondary_run_id,
        verification_id=verification_id,
        vlm_review_count=vlm_review_count,
    )


def process_book(
    store: SQLiteStore,
    project_id: int,
    *,
    primary_engine: OCREngine,
    secondary_engine: OCREngine,
    reviewer: VLMReviewer,
    crops_dir: Path,
) -> BookOutcome:
    """Process every imported page; a failed page never stops the rest of the book."""

    outcomes: list[PageOutcome] = []
    failed_pages: list[tuple[int, str]] = []
    for page in store.list_pages(project_id):
        try:
            outcomes.append(
                process_page(
                    store,
                    project_id,
                    page,
                    primary_engine=primary_engine,
                    secondary_engine=secondary_engine,
                    reviewer=reviewer,
                    crops_dir=crops_dir,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad page must not stop the book
            failed_pages.append(
                (page.page_index, str(exc).strip() or type(exc).__name__)
            )
    return BookOutcome(project_id, tuple(outcomes), tuple(failed_pages))
