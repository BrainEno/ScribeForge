from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from scribeforge.adapters.image_crops import materialize_review_crops
from scribeforge.domain.alignment import align_pages
from scribeforge.domain.crops import plan_review_crops
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import OCREngine
from scribeforge.domain.page_asset import PageAssetError, PageAssetEvidence, verify_page_asset
from scribeforge.domain.preprocess import PreprocessSettings
from scribeforge.domain.risk import assess_page, review_candidates
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.pipeline.adjudication import adjudicate_verification
from scribeforge.pipeline.preprocessing import preprocess_page
from scribeforge.pipeline.rounds import (
    RepairReader,
    RepairSettings,
    RoundReport,
    reader_for,
    repair_book,
)
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
    decision_count: int
    unresolved_pairs: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BookOutcome:
    project_id: int
    pages: tuple[PageOutcome, ...]
    failed_pages: tuple[tuple[int, str], ...]
    #: Repair rounds that ran after the first pass, in order. Empty when the
    #: caller did not ask for any, which keeps the old behaviour byte for byte.
    rounds: tuple[RoundReport, ...] = ()


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
    reviewer: VLMReviewer | None = None,
    crops_dir: Path,
    preprocess: PreprocessSettings | None = None,
) -> PageOutcome:
    """Run every remaining pipeline stage for one page, resumably and page-granular."""

    page_index = page.page_index
    image_path = page.source_image
    image_width = page.width
    image_height = page.height

    # Refuse to read a page raster that is not the one this project recorded.
    # A replaced or truncated page image fails here before any engine sees it,
    # so no decision is ever made from bytes nobody accounted for.
    if page.page_asset_sha256 is not None and page.page_asset_byte_size is not None:
        try:
            verify_page_asset(
                Path(image_path),
                PageAssetEvidence(
                    sha256=str(page.page_asset_sha256),
                    byte_size=int(page.page_asset_byte_size),
                    render_parameters={},
                    version=int(page.page_asset_version or 1),
                ),
                what=f"page {page_index} source image",
            )
        except PageAssetError as exc:
            raise PageAssetError(str(exc)) from None

    if preprocess is not None:
        prepared = preprocess_page(
            store,
            project_id,
            page,
            preprocess,
            assets_dir=crops_dir.parent / "preprocessed",
        )
        # Both the engines and the review crops must read the same frame, or the
        # normalized boxes the engines report will not line up with the pixels a
        # reviewer is shown.
        image_path = str(prepared.image_path)
        image_width = prepared.width
        image_height = prepared.height

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

    if reviewer is None:
        # No adjudication model is configured yet: leave the VLM stage untouched so a
        # later run that supplies a reviewer can still perform it.
        vlm_review_count = 0
    else:

        def compute_vlm() -> None:
            record = store.load_verification(verification_id)
            if not record.candidates:
                return
            plans = plan_review_crops(record.candidates, image_width, image_height)
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

    adjudication = adjudicate_verification(store, project_id, verification_id)

    return PageOutcome(
        page_index=page_index,
        primary_run_id=primary_run_id,
        secondary_run_id=secondary_run_id,
        verification_id=verification_id,
        vlm_review_count=vlm_review_count,
        decision_count=adjudication.recorded,
        unresolved_pairs=adjudication.unresolved,
    )


def _repair_readers(
    primary_engine: OCREngine, secondary_engine: OCREngine
) -> tuple[RepairReader, ...]:
    """Default repair readers: the two pipeline engines, re-reading one line.

    The same engines on a *different input* (an isolated, wider, enlarged crop)
    are a genuinely independent attempt: the first pass failed on the page, not on
    the crop. A third reader can be supplied by the caller when one is available.
    """

    return (reader_for(primary_engine), reader_for(secondary_engine))


def process_book(
    store: SQLiteStore,
    project_id: int,
    *,
    primary_engine: OCREngine,
    secondary_engine: OCREngine,
    reviewer: VLMReviewer | None = None,
    crops_dir: Path,
    preprocess: PreprocessSettings | None = None,
    repair: RepairSettings | None = None,
    repair_readers: Sequence[RepairReader] | None = None,
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
                    preprocess=preprocess,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad page must not stop the book
            failed_pages.append(
                (page.page_index, str(exc).strip() or type(exc).__name__)
            )

    rounds: tuple[RoundReport, ...] = ()
    if repair is not None:
        # Repair runs after the whole book, not per page: it needs book-wide
        # furniture detection and it must spend the remaining budget on the
        # riskiest *pairs of the book*, not on the page that happens to be first.
        rounds = repair_book(
            store,
            project_id,
            readers=repair_readers
            or _repair_readers(primary_engine, secondary_engine),
            crops_dir=crops_dir / "repair",
            reviewer=reviewer,
            settings=repair,
        )
    return BookOutcome(project_id, tuple(outcomes), tuple(failed_pages), rounds)
