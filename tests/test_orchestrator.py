from __future__ import annotations

from pathlib import Path

from PIL import Image

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.orchestrator import process_book, process_page
from scribeforge.storage.sqlite import PageRecord, SQLiteStore


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


class FakeReviewer:
    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading:
        return VLMReading(page_index, pair_index, "fake-vlm", "1.0", "verified", False)


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

    process_page(
        store, project_id, page,
        primary_engine=primary, secondary_engine=secondary,
        reviewer=FakeReviewer(), crops_dir=tmp_path / "crops",
    )

    job = store.load_job(running_id)
    assert job.state is JobState.SUCCEEDED
    assert job.attempts == 2


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
    ] == [("verified", "vlm")]


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
