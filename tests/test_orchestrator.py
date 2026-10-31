from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.image_preprocess import PreprocessError
from scribeforge.adapters.subprocess_ocr import OCREngineTimeoutError
from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    cancellation_scope,
)
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.page_asset import PageAssetError, PageAssetEvidence
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.adjudication import record_human_decision
from scribeforge.pipeline.orchestrator import process_book, process_page
from scribeforge.pipeline.preprocessing import preprocess_page
from scribeforge.storage.sqlite import JobAlreadyRunningError, PageRecord, SQLiteStore


class FakeEngine:
    """Deterministic OCR engine standing in for a real worker."""

    def __init__(self, name: str, text: str) -> None:
        self._name = name
        self._text = text

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        token = OCRToken(self._text, 0.9, box)
        line = OCRLine(self._text, box, (token,))
        return PageOCRResult(page_index, self._name, "1.0", (line,))


class ExplodingEngine:
    """Engine that fails on every call, simulating a crashed worker."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        raise RuntimeError("engine crashed")


class CancellingEngine:
    """Engine that receives an in-run cancellation request while reading a page."""

    def __init__(self, token: CancellationToken) -> None:
        self._token = token

    @property
    def name(self) -> str:
        return "primary"

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        self._token.cancel()
        self._token.raise_if_cancelled()
        raise AssertionError("cancelled engine unexpectedly returned")


class FakeReviewer:
    """A reviewer that corroborates the primary engine's reading.

    A vision model's answer is only accepted when it agrees with one of the
    engines (issue #76), so a fake that invented a third text would leave every
    pair unresolved and test the wrong thing.
    """

    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading:
        return VLMReading(page_index, pair_index, "fake-vlm", "1.0", "鸦", False)


class UncertainReviewer:
    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading:
        return VLMReading(page_index, pair_index, "fake-vlm", "1.0", "maybe", True)


def _store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    return store, store.create_project("Book", "book.pdf", "source-hash")


def _seed_page(store: SQLiteStore, project_id: int, tmp_path: Path) -> PageRecord:
    image_path = tmp_path / f"page-{project_id}.png"
    Image.new("RGB", (100, 150), "white").save(image_path)
    store.add_page(project_id, 0, str(image_path), 100, 150)
    return store.list_pages(project_id)[0]


def _engines() -> tuple[FakeEngine, FakeEngine]:
    # Disagreeing text produces a substitution conflict -> one review candidate.
    return FakeEngine("primary", "鸦"), FakeEngine("secondary", "雅")


def _stage_states(store: SQLiteStore, project_id: int, page_index: int) -> dict[JobStage, JobState]:
    states: dict[JobStage, JobState] = {}
    for stage in (
        JobStage.PRIMARY_OCR,
        JobStage.SECONDARY_OCR,
        JobStage.VERIFY,
        JobStage.VLM_REVIEW,
    ):
        job_id = store.ensure_job(project_id, stage, page_index=page_index)
        states[stage] = store.load_job(job_id).state
    return states


def test_process_page_runs_every_stage_and_persists_evidence(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)
    primary, secondary = _engines()

    outcome = process_page(
        store,
        project_id,
        page,
        primary_engine=primary,
        secondary_engine=secondary,
        reviewer=FakeReviewer(),
        crops_dir=tmp_path / "crops",
    )

    assert outcome.page_index == 0
    assert outcome.primary_run_id > 0
    assert outcome.secondary_run_id > 0
    assert outcome.verification_id > 0
    assert outcome.vlm_review_count == 1

    states = _stage_states(store, project_id, 0)
    assert all(state is JobState.SUCCEEDED for state in states.values())
    assert len(store.list_vlm_reviews(outcome.verification_id)) == 1
    assert store.reviewed_pair_indexes(outcome.verification_id) == frozenset({0})


def test_process_page_resumes_without_recomputing_succeeded_stages(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)
    primary, secondary = _engines()

    first = process_page(
        store, project_id, page,
        primary_engine=primary, secondary_engine=secondary,
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    # Every stage already succeeded: engines must not be invoked again.
    second = process_page(
        store, project_id, page,
        primary_engine=ExplodingEngine("primary"), secondary_engine=ExplodingEngine("secondary"),
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    assert second.primary_run_id == first.primary_run_id
    assert second.secondary_run_id == first.secondary_run_id
    assert second.verification_id == first.verification_id
    assert second.vlm_review_count == 1


def test_process_page_resumes_interrupted_running_stage(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)
    primary, secondary = _engines()

    # Simulate a crash that left the primary OCR job in running state.
    running_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    store.start_job(running_id)
    assert store.load_job(running_id).state is JobState.RUNNING
    # Releasing the crashed process's handle makes the persisted RUNNING state
    # safely reclaimable; a second live owner must not be treated as interrupted.
    store.close()
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()

    process_page(
        store, project_id, page,
        primary_engine=primary, secondary_engine=secondary,
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    job = store.load_job(running_id)
    assert job.state is JobState.SUCCEEDED
    assert job.attempts == 2


def test_process_book_does_not_misreport_an_owned_job_as_a_failed_page(
    tmp_path: Path,
) -> None:
    owner_store, project_id = _store(tmp_path)
    page = _seed_page(owner_store, project_id, tmp_path)
    owned_job = owner_store.ensure_job(
        project_id, JobStage.PRIMARY_OCR, page_index=page.page_index
    )
    owner_store.start_job(owned_job)

    contender_store = SQLiteStore(tmp_path / "project.sqlite3")
    contender_store.initialize()
    primary, secondary = _engines()

    with pytest.raises(JobAlreadyRunningError, match="owns the project writer lock"):
        process_book(
            contender_store,
            project_id,
            primary_engine=primary,
            secondary_engine=secondary,
            crops_dir=tmp_path / "crops",
        )

    assert owner_store.load_job(owned_job).state is JobState.RUNNING
    assert owner_store.load_job(owned_job).attempts == 1
    owner_store.fail_job(owned_job, "cleanup after ownership assertion")


def test_process_page_skips_vlm_when_engines_agree(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)

    outcome = process_page(
        store, project_id, page,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "鸦"),
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    assert outcome.vlm_review_count == 0
    assert store.list_vlm_reviews(outcome.verification_id) == ()
    assert _stage_states(store, project_id, 0)[JobStage.VLM_REVIEW] is JobState.SUCCEEDED


def test_process_page_without_reviewer_defers_vlm_stage(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)

    outcome = process_page(
        store, project_id, page,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        crops_dir=tmp_path / "crops",
    )

    assert outcome.verification_id > 0
    assert outcome.vlm_review_count == 0
    assert JobStage.VLM_REVIEW not in {job.stage for job in store.list_jobs(project_id)}


def test_process_page_records_automatic_decision_when_engines_agree(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)

    outcome = process_page(
        store, project_id, page,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "鸦"),
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    assert outcome.decision_count == 1
    assert outcome.unresolved_pairs == ()
    assert [
        (decision.selected_text, decision.actor)
        for decision in store.list_decisions(project_id, 0, 0)
    ] == [("鸦", "automatic")]


def test_process_page_records_vlm_decision_from_confident_review(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)

    outcome = process_page(
        store, project_id, page,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    assert outcome.decision_count == 1
    assert outcome.unresolved_pairs == ()
    assert [
        (decision.selected_text, decision.actor)
        for decision in store.list_decisions(project_id, 0, 0)
    ] == [("鸦", "vlm")]


def test_process_page_queues_uncertain_review_for_humans(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)

    outcome = process_page(
        store, project_id, page,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        reviewer=UncertainReviewer(), crops_dir=tmp_path / "crops",
    )

    assert outcome.decision_count == 0
    assert outcome.unresolved_pairs == (0,)
    assert store.list_decisions(project_id, 0, 0) == ()


def test_process_book_isolates_failed_page_and_resumes(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _seed_page(store, project_id, tmp_path)

    primary, secondary = _engines()
    book = process_book(
        store, project_id,
        primary_engine=primary, secondary_engine=ExplodingEngine("secondary"),
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    assert book.failed_pages == ((0, "engine crashed"),)
    assert book.pages == ()
    assert _stage_states(store, project_id, 0)[JobStage.PRIMARY_OCR] is JobState.SUCCEEDED
    assert _stage_states(store, project_id, 0)[JobStage.SECONDARY_OCR] is JobState.FAILED

    # The failed page is retryable; a healthy secondary engine completes it.
    resumed = process_book(
        store, project_id,
        primary_engine=primary, secondary_engine=secondary,
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    assert resumed.failed_pages == ()
    assert [outcome.page_index for outcome in resumed.pages] == [0]
    assert all(
        state is JobState.SUCCEEDED for state in _stage_states(store, project_id, 0).values()
    )


def test_worker_timeout_is_visible_and_retryable(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _seed_page(store, project_id, tmp_path)

    class TimedOutEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            raise OCREngineTimeoutError(
                f"OCR worker {self._name} timed out on page {page_index + 1} after 1 seconds"
            )

    first = process_book(
        store,
        project_id,
        primary_engine=TimedOutEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        crops_dir=tmp_path / "crops",
    )

    assert first.failed_pages == ((0, "OCR worker primary timed out on page 1 after 1 seconds"),)
    primary_job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    failed_job = store.load_job(primary_job_id)
    assert failed_job.state is JobState.FAILED
    assert failed_job.last_error == first.failed_pages[0][1]

    retry = process_book(
        store,
        project_id,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        crops_dir=tmp_path / "crops",
    )

    assert retry.failed_pages == ()
    assert store.load_job(primary_job_id).state is JobState.SUCCEEDED
    assert store.load_job(primary_job_id).attempts == 2


def test_cancelled_page_is_failed_retryable_and_preserves_prior_evidence(
    tmp_path: Path,
) -> None:
    store, project_id = _store(tmp_path)
    first_page = _seed_page(store, project_id, tmp_path)
    second_image = tmp_path / "page-1.png"
    Image.new("RGB", (100, 150), "white").save(second_image)
    store.add_page(project_id, 1, str(second_image), 100, 150)

    first_outcome = process_page(
        store,
        project_id,
        first_page,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        crops_dir=tmp_path / "crops",
    )
    record_human_decision(store, project_id, 0, 0, "人工裁定")
    first_page_bytes = Path(first_page.source_image).read_bytes()
    first_decisions = store.list_decisions(project_id, 0, 0)
    token = CancellationToken()

    with cancellation_scope(token), pytest.raises(ProcessingCancelled, match="cancel"):
        process_book(
            store,
            project_id,
            primary_engine=CancellingEngine(token),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
        )

    cancelled_job = store.load_job(
        store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=1)
    )
    secondary_job = store.load_job(
        store.ensure_job(project_id, JobStage.SECONDARY_OCR, page_index=1)
    )
    assert cancelled_job.state is JobState.FAILED
    assert "cancel" in (cancelled_job.last_error or "").lower()
    assert secondary_job.state is JobState.PENDING
    assert Path(first_page.source_image).read_bytes() == first_page_bytes
    assert store.list_decisions(project_id, 0, 0) == first_decisions
    assert store.load_verification(first_outcome.verification_id).primary_run_id == (
        first_outcome.primary_run_id
    )

    retried = process_book(
        store,
        project_id,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        crops_dir=tmp_path / "crops",
    )

    assert retried.failed_pages == ()
    assert [page.page_index for page in retried.pages] == [0, 1]
    assert store.load_verification(first_outcome.verification_id).secondary_run_id == (
        first_outcome.secondary_run_id
    )
    assert store.list_decisions(project_id, 0, 0) == first_decisions
    assert store.latest_decision(project_id, 0, 0).selected_text == "人工裁定"
    assert store.latest_decision(project_id, 0, 0).actor == "human"
    assert Path(first_page.source_image).read_bytes() == first_page_bytes
    assert store.load_job(cancelled_job.id).state is JobState.SUCCEEDED
    assert store.load_job(cancelled_job.id).attempts == 2


class RecordingEngine(FakeEngine):
    """Engine that remembers which raster it was asked to read."""

    def __init__(self, name: str, text: str) -> None:
        super().__init__(name, text)
        self.seen: list[tuple[str, int]] = []

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        self.seen.append((image_path, page_index))
        return super().analyze_page(image_path, page_index)


def test_process_page_runs_preprocess_before_ocr_and_feeds_it_the_corrected_scan(
    tmp_path: Path,
) -> None:
    """Both engines must read the preprocessed raster, not the imported original.

    Sending engines the raw scan while preprocessing exists would make the stage
    decorative; the whole point is that OCR sees the corrected frame.
    """

    from scribeforge.domain.preprocess import PreprocessOperation, PreprocessSettings

    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)
    primary, secondary = RecordingEngine("primary", "鸦"), RecordingEngine("secondary", "鸦")

    process_page(
        store,
        project_id,
        page,
        primary_engine=primary,
        secondary_engine=secondary,
        crops_dir=tmp_path / "crops",
        # Adapt-to-page is disabled so the transform is guaranteed to run: the
        # point of this test is the wiring, not the degradation gate.
        preprocess=PreprocessSettings(
            operations=(PreprocessOperation.DESHADOW,), adapt_to_page=False
        ),
    )

    expected = store.load_preprocess(project_id, 0)
    assert expected is not None
    assert expected.applied == ("deshadow",)
    assert primary.seen and secondary.seen
    assert primary.seen[0][0] == expected.output_image
    assert secondary.seen[0][0] == expected.output_image
    assert Path(expected.output_image).is_file()
    # The imported original stays byte-identical: it is the evidence of record.
    assert page.source_image != expected.output_image
    assert Path(page.source_image).is_file()


def test_process_page_without_preprocess_reads_the_original_scan(tmp_path: Path) -> None:
    """Opting out must leave the earlier behaviour byte-for-byte unchanged."""

    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)
    primary, secondary = RecordingEngine("primary", "鸦"), RecordingEngine("secondary", "鸦")

    process_page(
        store,
        project_id,
        page,
        primary_engine=primary,
        secondary_engine=secondary,
        crops_dir=tmp_path / "crops",
    )

    assert primary.seen[0][0] == page.source_image
    assert store.load_preprocess(project_id, 0) is None
    states = _stage_states(store, project_id, 0)
    preprocess_job = store.ensure_job(project_id, JobStage.PREPROCESS, page_index=0)
    assert store.load_job(preprocess_job).state is JobState.PENDING
    assert states[JobStage.PRIMARY_OCR] is JobState.SUCCEEDED


def test_preprocess_is_idempotent_and_never_double_rotates(tmp_path: Path) -> None:
    """A second run reuses the stored raster instead of transforming it again."""

    from scribeforge.domain.preprocess import PreprocessOperation, PreprocessSettings

    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)
    settings = PreprocessSettings(operations=(PreprocessOperation.DESHADOW,))

    first = preprocess_page(
        store, project_id, page, settings, assets_dir=tmp_path / "preprocessed"
    )
    first_bytes = first.image_path.read_bytes()
    second = preprocess_page(
        store, project_id, page, settings, assets_dir=tmp_path / "preprocessed"
    )

    assert second.image_path == first.image_path
    assert second.image_path.read_bytes() == first_bytes
    assert second.outcome.output_sha256 == first.outcome.output_sha256
    job = store.ensure_job(project_id, JobStage.PREPROCESS, page_index=0)
    # Attempts must not climb on a cache hit.
    assert store.load_job(job).attempts == 1


def test_preprocess_rebuilds_when_the_raster_was_deleted(tmp_path: Path) -> None:
    """A stage that claims success but has no raster must not lie to the caller."""

    from scribeforge.domain.preprocess import PreprocessOperation, PreprocessSettings

    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)
    settings = PreprocessSettings(operations=(PreprocessOperation.DESHADOW,))
    first = preprocess_page(
        store, project_id, page, settings, assets_dir=tmp_path / "preprocessed"
    )
    first.image_path.unlink()

    rebuilt = preprocess_page(
        store, project_id, page, settings, assets_dir=tmp_path / "preprocessed"
    )

    assert rebuilt.image_path.is_file()
    assert rebuilt.outcome.output_sha256 == first.outcome.output_sha256


def test_preprocess_failure_marks_the_stage_failed_and_does_not_checkpoint_ocr(
    tmp_path: Path,
) -> None:
    """A corrupt scan must fail loudly at preprocess, not reach the engines."""

    from scribeforge.domain.preprocess import PreprocessSettings

    store, project_id = _store(tmp_path)
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    store.add_page(project_id, 0, str(corrupt), 100, 150)
    page = store.list_pages(project_id)[0]
    primary, secondary = RecordingEngine("primary", "鸦"), RecordingEngine("secondary", "鸦")

    with pytest.raises(PreprocessError):
        process_page(
            store,
            project_id,
            page,
            primary_engine=primary,
            secondary_engine=secondary,
            crops_dir=tmp_path / "crops",
            preprocess=PreprocessSettings(),
        )

    job = store.ensure_job(project_id, JobStage.PREPROCESS, page_index=0)
    assert store.load_job(job).state is JobState.FAILED
    assert store.load_job(job).last_error
    assert primary.seen == []
    ocr_job = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    assert store.load_job(ocr_job).state is JobState.PENDING


def test_review_crops_use_the_preprocessed_frame(tmp_path: Path) -> None:
    """Crops must come from the same frame that produced the normalized boxes."""

    from scribeforge.domain.preprocess import PreprocessOperation, PreprocessSettings

    store, project_id = _store(tmp_path)
    page = _seed_page(store, project_id, tmp_path)
    primary, secondary = _engines()

    process_page(
        store,
        project_id,
        page,
        primary_engine=primary,
        secondary_engine=secondary,
        reviewer=FakeReviewer(),
        crops_dir=tmp_path / "crops",
        preprocess=PreprocessSettings(
            operations=(PreprocessOperation.CROP_BORDER,),
            border_threshold=8,
        ),
    )

    crops = list((tmp_path / "crops").glob("*.png"))
    assert crops
    with Image.open(crops[0]) as crop:
        # The crop is cut from the preprocessed (cropped) frame, so it can never
        # be wider than the corrected page itself.
        corrected = store.load_preprocess(project_id, 0)
        assert corrected is not None
        assert crop.size[0] <= corrected.output_width


def test_process_page_refuses_replaced_page_raster(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    image_path = tmp_path / "page-0.png"
    image_path.write_bytes(b"original page bytes")
    store.add_page(
        project_id,
        0,
        str(image_path),
        100,
        150,
        page_asset=PageAssetEvidence.from_bytes(b"original page bytes"),
    )
    page = store.list_pages(project_id)[0]

    image_path.write_bytes(b"replacement page bytes")
    primary, secondary = _engines()

    with pytest.raises(PageAssetError, match="not the raster this project recorded"):
        process_page(
            store,
            project_id,
            page,
            primary_engine=primary,
            secondary_engine=secondary,
            reviewer=None,
            crops_dir=tmp_path / "crops",
        )
