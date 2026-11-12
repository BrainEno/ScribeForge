from __future__ import annotations

import contextvars
import shutil
import tempfile
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from scribeforge.adapters.image_crops import materialize_review_crops
from scribeforge.domain.alignment import align_pages
from scribeforge.domain.cancellation import ProcessingCancelled, raise_if_cancelled
from scribeforge.domain.crops import plan_review_crops
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import OCREngine
from scribeforge.domain.page_asset import PageAssetError, verify_page_asset
from scribeforge.domain.preprocess import PreprocessSettings
from scribeforge.domain.risk import assess_page, review_candidates
from scribeforge.domain.scheduling import (
    per_page_vram_mib,
    resolve_cpu_workers,
    resolve_page_worker_count,
)
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.pipeline.adjudication import adjudicate_verification
from scribeforge.pipeline.crop_evidence import crop_evidence, verified_vlm_crop
from scribeforge.pipeline.events import EventLog
from scribeforge.pipeline.ocr_frame import capture_frame, verification_frame
from scribeforge.pipeline.preprocessing import preprocess_page
from scribeforge.pipeline.rounds import (
    RepairReader,
    RepairSettings,
    RoundReport,
    reader_for,
    repair_book,
)
from scribeforge.pipeline.vlm_review import review_pending_candidates
from scribeforge.storage.sqlite import JobAlreadyRunningError, PageRecord, SQLiteStore
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
    *,
    event_log: EventLog | None = None,
) -> T:
    """Drive one page-scoped stage, skipping it when it already succeeded.

    A stage persists its evidence before returning; on failure it records the
    error so the stage stays resumable and does not invalidate the whole book.
    When ``event_log`` is given, a ``stage_finished`` event carries the wall
    time of the executed stage (skipped stages emit nothing).
    """

    raise_if_cancelled()
    job_id = store.ensure_job(project_id, stage, page_index=page_index)
    if store.load_job(job_id).state is not JobState.SUCCEEDED:
        store.start_job(job_id)
        stage_started = time.monotonic()
        try:
            compute()
        except Exception as exc:
            store.fail_job(job_id, str(exc).strip() or type(exc).__name__)
            raise
        store.complete_job(job_id)
        if event_log is not None:
            event_log.emit(
                "stage_finished",
                stage=stage.value,
                page_index=page_index,
                elapsed_seconds=round(time.monotonic() - stage_started, 6),
            )
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
    event_log: EventLog | None = None,
) -> PageOutcome:
    """Run every remaining pipeline stage for one page, resumably and page-granular."""

    raise_if_cancelled()
    store.require_current_page(project_id, page)
    page_index = page.page_index
    image_path = page.source_image
    image_width = page.width
    image_height = page.height

    # Refuse to read a page raster that is not the one this project recorded.
    # A replaced or truncated page image fails here before any engine sees it,
    # so no decision is ever made from bytes nobody accounted for.
    evidence = page.asset_evidence()
    if evidence is not None:
        verify_page_asset(Path(image_path), evidence, what=f"page {page_index} source image")

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

    frame = capture_frame(
        Path(image_path),
        "preprocessed" if preprocess is not None else "original",
        image_width,
        image_height,
    )

    primary_name = primary_engine.name
    secondary_name = secondary_engine.name

    def read_engine(engine: OCREngine, expected_name: str) -> None:
        if capture_frame(Path(image_path), frame.kind, frame.width, frame.height) != frame:
            raise PageAssetError("OCR frame changed before reading")
        with tempfile.TemporaryDirectory(prefix="scribeforge-ocr-input-") as temporary_dir:
            scratch = Path(temporary_dir) / Path(image_path).name
            shutil.copyfile(image_path, scratch)
            try:
                copied = capture_frame(scratch, frame.kind, frame.width, frame.height)
                source = capture_frame(Path(image_path), frame.kind, frame.width, frame.height)
            except (OSError, ValueError) as exc:
                raise PageAssetError("OCR frame changed while preparing the worker input") from exc
            if copied != frame or source != frame:
                raise PageAssetError("OCR frame changed while preparing the worker input")
            result = engine.analyze_page(str(scratch), page_index)
            if result.page_index != page_index:
                raise ValueError(
                    f"OCR page mismatch: expected {page_index}, got {result.page_index}"
                )
            if result.engine != expected_name:
                raise ValueError(
                    f"OCR engine mismatch: expected {expected_name!r}, got {result.engine!r}"
                )
            try:
                copied = capture_frame(scratch, frame.kind, frame.width, frame.height)
                source = capture_frame(Path(image_path), frame.kind, frame.width, frame.height)
            except (OSError, ValueError) as exc:
                raise PageAssetError("OCR frame changed during reading") from exc
            if copied != frame or source != frame:
                raise PageAssetError("OCR frame changed during reading")
        store.record_ocr(project_id, result, frame=frame, expected_page=page)

    def compute_primary() -> None:
        read_engine(primary_engine, primary_name)

    primary_run_id = _run_stage(
        store,
        project_id,
        JobStage.PRIMARY_OCR,
        page_index,
        compute_primary,
        lambda: store.latest_engine_run(project_id, page_index, primary_name),
        event_log=event_log,
    )

    if store.load_ocr_frame(primary_run_id) != frame:
        raise ValueError("primary OCR frame mismatch or unknown; re-import into a new project")

    def compute_secondary() -> None:
        read_engine(secondary_engine, secondary_name)

    secondary_run_id = _run_stage(
        store,
        project_id,
        JobStage.SECONDARY_OCR,
        page_index,
        compute_secondary,
        lambda: store.latest_engine_run(project_id, page_index, secondary_name),
        event_log=event_log,
    )

    if store.load_ocr_frame(secondary_run_id) != frame:
        raise ValueError("secondary OCR frame mismatch or unknown; re-import into a new project")

    def compute_verify() -> None:
        primary = store.load_ocr_run(primary_run_id)
        secondary = store.load_ocr_run(secondary_run_id)
        alignment = align_pages(primary, secondary)
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        store.record_verification(primary_run_id, secondary_run_id, alignment, risks, candidates)

    verification_id = _run_stage(
        store,
        project_id,
        JobStage.VERIFY,
        page_index,
        compute_verify,
        lambda: store.latest_verification_id(project_id, page_index),
        event_log=event_log,
    )

    verified_frame, _ = verification_frame(store, project_id, page, verification_id)

    def validate_vlm_evidence() -> None:
        for review in store.list_vlm_reviews(verification_id):
            verified_vlm_crop(store, verification_id, verified_frame, review)

    validate_vlm_evidence()

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
            crop_paths = materialize_review_crops(
                Path(image_path), plans, crops_dir, immutable=True
            )
            verification_frame(store, project_id, page, verification_id)
            crop_inputs = {
                plan.pair_index: crop_evidence(
                    crop_paths[plan.pair_index], plan, verified_frame, verification_id
                )
                for plan in plans
            }
            state = SQLiteVLMReviewState(store, verification_id, expected_page=page)
            review_pending_candidates(
                record.candidates, crop_paths, reviewer, state, crop_inputs=crop_inputs
            )
            verification_frame(store, project_id, page, verification_id)

        vlm_review_count = _run_stage(
            store,
            project_id,
            JobStage.VLM_REVIEW,
            page_index,
            compute_vlm,
            lambda: len(store.reviewed_pair_indexes(verification_id)),
            event_log=event_log,
        )

    validate_vlm_evidence()
    adjudication = adjudicate_verification(store, project_id, verification_id, expected_page=page)

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

    A different crop is a new attempt, not an independent source. Consensus
    counts the engine once per candidate even when it reads both page and crop.
    A third reader can be supplied by the caller when one is available.
    """

    return (reader_for(primary_engine), reader_for(secondary_engine))


def _run_one_page(
    store: SQLiteStore,
    project_id: int,
    page: PageRecord,
    *,
    primary_engine: OCREngine,
    secondary_engine: OCREngine,
    reviewer: VLMReviewer | None,
    crops_dir: Path,
    preprocess: PreprocessSettings | None,
    event_log: EventLog | None = None,
) -> tuple[PageOutcome | None, tuple[int, str] | None]:
    """Run one page, returning either its outcome or a (page_index, error) pair.

    Cancellation and writer-lock conflicts are re-raised because they describe
    the whole run, not a single page. When ``event_log`` is given, page_started
    and page_finished/page_failed events (with elapsed seconds) are emitted.
    """
    if event_log is not None:
        event_log.emit("page_started", page_index=page.page_index)
    started = time.monotonic()
    try:
        raise_if_cancelled()
        outcome = process_page(
            store,
            project_id,
            page,
            primary_engine=primary_engine,
            secondary_engine=secondary_engine,
            reviewer=reviewer,
            crops_dir=crops_dir,
            preprocess=preprocess,
            event_log=event_log,
        )
    except (ProcessingCancelled, JobAlreadyRunningError):
        # A busy project is not a failed page: another process still owns its
        # writer lock, and recording a page failure here would misstate the
        # active owner's work. Let the CLI report this as a retryable conflict.
        raise
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the book
        if event_log is not None:
            event_log.emit(
                "page_failed",
                page_index=page.page_index,
                error=str(exc).strip() or type(exc).__name__,
                elapsed_seconds=round(time.monotonic() - started, 6),
            )
        return None, (page.page_index, str(exc).strip() or type(exc).__name__)
    if event_log is not None:
        event_log.emit(
            "page_finished",
            page_index=page.page_index,
            elapsed_seconds=round(time.monotonic() - started, 6),
        )
    return outcome, None


def _run_pages_parallel(
    store: SQLiteStore,
    project_id: int,
    pages: Sequence[PageRecord],
    workers: int,
    *,
    primary_engine: OCREngine,
    secondary_engine: OCREngine,
    reviewer: VLMReviewer | None,
    crops_dir: Path,
    preprocess: PreprocessSettings | None,
    event_log: EventLog | None = None,
) -> tuple[list[PageOutcome], list[tuple[int, str]]]:
    """Process pages on a bounded thread pool, preserving page order in the result.

    ``SQLiteStore`` opens a fresh connection per call, so concurrent page jobs
    are safe; each page claims its own (project, stage, page) job row.
    """
    submitted: dict[Future[tuple[PageOutcome | None, tuple[int, str] | None]], int] = {}
    outcomes: list[PageOutcome | None] = [None] * len(pages)
    failures: list[tuple[int, str] | None] = [None] * len(pages)

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scribeforge-page")
    try:
        for index, page in enumerate(pages):
            # Pool workers start with an empty context, so the cancellation
            # ContextVar would be invisible to them; run each page inside a copy
            # of the caller's context so cancellation propagates to every page.
            context = contextvars.copy_context()
            page = pages[index]

            def task(
                ctx: contextvars.Context = context, p: PageRecord = page
            ) -> tuple[PageOutcome | None, tuple[int, str] | None]:
                return ctx.run(
                    _run_one_page,
                    store,
                    project_id,
                    p,
                    primary_engine=primary_engine,
                    secondary_engine=secondary_engine,
                    reviewer=reviewer,
                    crops_dir=crops_dir,
                    preprocess=preprocess,
                    event_log=event_log,
                )

            future = pool.submit(task)
            submitted[future] = index
        for future, index in submitted.items():
            # ProcessingCancelled / JobAlreadyRunningError propagate unchanged,
            # matching the serial path. The finally block below does NOT wait for
            # still-running pages: on cancellation the caller (CLI) tears down the
            # engine subprocesses itself, so lingering page threads must not delay
            # the cancellation from reaching the user.
            outcome, failure = future.result()
            outcomes[index] = outcome
            failures[index] = failure
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    ordered_outcomes = [o for o in outcomes if o is not None]
    ordered_failures = [f for f in failures if f is not None]
    return ordered_outcomes, ordered_failures


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
    max_workers: int = 0,
    gpu_memory_mib: int | None = None,
    event_log: EventLog | None = None,
) -> BookOutcome:
    """Process every imported page; a failed page never stops the rest of the book.

    Pages are independent, so with ``max_workers > 1`` they run on a bounded
    thread pool (the OCR engines are subprocesses, making pages I/O-bound).
    ``max_workers=0`` picks half the CPU cores, and ``max_workers=1`` keeps the
    original serial behaviour. Outcomes are returned in page order regardless of
    completion order, and the exception taxonomy of the serial path is unchanged:
    cancellation and writer-lock conflicts propagate, any other per-page failure
    is collected into ``failed_pages``.

    ``gpu_memory_mib`` caps the worker count by a GPU memory budget: each
    in-flight page costs the sum of its engines' declared ``vram_mib`` (or a
    conservative default), and at most ``budget // per_page`` pages run at once.
    """

    workers = resolve_cpu_workers(max_workers)
    if gpu_memory_mib is not None and gpu_memory_mib < 1:
        raise ValueError(f"gpu_memory_mib must be >= 1 when set, got {gpu_memory_mib}")
    if gpu_memory_mib is not None:
        # On GPU hosts each in-flight page keeps its engines resident in VRAM,
        # so the page worker count is also capped by the memory budget.
        workers = resolve_page_worker_count(
            workers,
            memory_budget_mib=gpu_memory_mib,
            per_page_vram_mib=per_page_vram_mib((primary_engine, secondary_engine)),
        )
    pages = store.list_pages(project_id)

    book_started = time.monotonic()
    if event_log is not None:
        event_log.emit(
            "book_started",
            project_id=project_id,
            pages=len(pages),
            max_workers=workers,
            gpu_memory_mib=gpu_memory_mib,
        )

    outcomes: list[PageOutcome] = []
    failed_pages: list[tuple[int, str]] = []
    try:
        if workers == 1:
            for page in pages:
                outcome, failure = _run_one_page(
                    store,
                    project_id,
                    page,
                    primary_engine=primary_engine,
                    secondary_engine=secondary_engine,
                    reviewer=reviewer,
                    crops_dir=crops_dir,
                    preprocess=preprocess,
                    event_log=event_log,
                )
                if failure is not None:
                    failed_pages.append(failure)
                elif outcome is not None:
                    outcomes.append(outcome)
        else:
            outcomes, failed_pages = _run_pages_parallel(
                store,
                project_id,
                pages,
                workers,
                primary_engine=primary_engine,
                secondary_engine=secondary_engine,
                reviewer=reviewer,
                crops_dir=crops_dir,
                preprocess=preprocess,
                event_log=event_log,
            )
    except BaseException:
        if event_log is not None:
            event_log.emit(
                "book_aborted",
                project_id=project_id,
                succeeded=len(outcomes),
                failed=len(failed_pages),
                elapsed_seconds=round(time.monotonic() - book_started, 6),
            )
        raise

    if event_log is not None:
        event_log.emit(
            "book_finished",
            project_id=project_id,
            succeeded=len(outcomes),
            failed=len(failed_pages),
            elapsed_seconds=round(time.monotonic() - book_started, 6),
        )

    raise_if_cancelled()
    rounds: tuple[RoundReport, ...] = ()
    if repair is not None:
        # Repair runs after the whole book, not per page: it needs book-wide
        # furniture detection and it must spend the remaining budget on the
        # riskiest *pairs of the book*, not on the page that happens to be first.
        rounds = repair_book(
            store,
            project_id,
            readers=repair_readers or _repair_readers(primary_engine, secondary_engine),
            crops_dir=crops_dir / "repair",
            reviewer=reviewer,
            settings=repair,
        )
    return BookOutcome(project_id, tuple(outcomes), tuple(failed_pages), rounds)
