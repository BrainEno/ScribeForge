"""Page-level parallel processing tests (issue #70).

`process_book(max_workers=N)` runs independent pages on a bounded thread pool
(the OCR engines are subprocesses, so pages are I/O-bound and threads suffice).
The serial path (`max_workers=1`) must stay bit-identical, outcomes keep page
order, one failed page never stops the book, and cancellation still propagates.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    cancellation_scope,
)
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.storage.sqlite import JobAlreadyRunningError, SQLiteStore


class FakeEngine:
    def __init__(self, name: str, text: str = "鸦") -> None:
        self._name = name
        self._text = text
        self.calls: list[int] = []
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        with self._lock:
            self.calls.append(page_index)
        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        token = OCRToken(self._text, 0.9, box)
        line = OCRLine(self._text, box, (token,))
        return PageOCRResult(page_index, self._name, "1.0", (line,))


class SlowEngine(FakeEngine):
    """Sleeps per call so serial vs parallel wall time is measurable."""

    def __init__(self, name: str, delay: float, barrier: threading.Barrier | None = None) -> None:
        super().__init__(name)
        self._delay = delay
        self._barrier = barrier

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        result = super().analyze_page(image_path, page_index)
        if self._barrier is not None:
            # Deadlocks (times out) unless all N pages really run concurrently.
            self._barrier.wait(timeout=10)
        else:
            time.sleep(self._delay)
        return result


class SelectiveFailureEngine(FakeEngine):
    def __init__(self, name: str, failing_pages: set[int]) -> None:
        super().__init__(name)
        self._failing = failing_pages

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        if page_index in self._failing:
            raise RuntimeError(f"engine crashed on page {page_index}")
        return super().analyze_page(image_path, page_index)


class CancellingEngine(FakeEngine):
    def __init__(self, token: CancellationToken) -> None:
        super().__init__("primary")
        self._token = token

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        self._token.cancel()
        self._token.raise_if_cancelled()
        raise AssertionError("unreachable")


def _store(tmp_path: Path, page_count: int) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    for index in range(page_count):
        image_path = tmp_path / f"page-{index}.png"
        Image.new("RGB", (100, 150), "white").save(image_path)
        store.add_page(project_id, index, str(image_path), 100, 150)
    return store, project_id


def _run(store: SQLiteStore, project_id: int, tmp_path: Path, primary, secondary, **kwargs):
    return process_book(
        store,
        project_id,
        primary_engine=primary,
        secondary_engine=secondary,
        crops_dir=tmp_path / "crops",
        **kwargs,
    )


def test_parallel_pages_really_run_concurrently(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 4)
    barrier = threading.Barrier(4)
    primary = SlowEngine("primary", 0.0, barrier=barrier)
    secondary = SlowEngine("secondary", 0.0, barrier=barrier)

    started = time.monotonic()
    outcome = _run(store, project_id, tmp_path, primary, secondary, max_workers=4)
    elapsed = time.monotonic() - started

    assert len(outcome.pages) == 4
    assert outcome.failed_pages == ()
    # All four pages hit the barrier at once; a serial run would deadlock and
    # the barrier would raise BrokenBarrierError after the 10s timeout.
    assert elapsed < 10


def test_parallel_outcomes_preserve_page_order(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 6)

    class IndexedEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            box = BoundingBox(0.1, 0.1, 0.8, 0.02)
            token = OCRToken(f"text-{page_index}", 0.9, box)
            line = OCRLine(f"text-{page_index}", box, (token,))
            return PageOCRResult(page_index, self.name, "1.0", (line,))

    outcome = _run(
        store,
        project_id,
        tmp_path,
        IndexedEngine("primary"),
        IndexedEngine("secondary"),
        max_workers=3,
    )

    assert [page.page_index for page in outcome.pages] == [0, 1, 2, 3, 4, 5]


def test_parallel_failure_does_not_stop_other_pages(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 4)
    primary = SelectiveFailureEngine("primary", failing_pages={1})
    secondary = FakeEngine("secondary", "雅")

    outcome = _run(store, project_id, tmp_path, primary, secondary, max_workers=4)

    assert [page.page_index for page in outcome.pages] == [0, 2, 3]
    assert len(outcome.failed_pages) == 1
    assert outcome.failed_pages[0][0] == 1
    assert "engine crashed on page 1" in outcome.failed_pages[0][1]


def test_parallel_cancellation_propagates(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 4)
    token = CancellationToken()
    with cancellation_scope(token), pytest.raises(ProcessingCancelled):
        _run(
            store,
            project_id,
            tmp_path,
            CancellingEngine(token),
            FakeEngine("secondary", "雅"),
            max_workers=2,
        )


def test_max_workers_must_be_positive(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 1)
    for bad in (-1, -5):
        with pytest.raises(ValueError, match="max_workers"):
            _run(
                store,
                project_id,
                tmp_path,
                FakeEngine("primary"),
                FakeEngine("secondary"),
                max_workers=bad,
            )


def test_serial_default_matches_parallel_results(tmp_path: Path) -> None:
    def build(root: Path):
        store, project_id = _store(root, 3)
        outcome = _run(
            store,
            project_id,
            root,
            FakeEngine("primary", "鸦"),
            FakeEngine("secondary", "雅"),
            **kwargs,
        )
        return outcome

    kwargs: dict = {"max_workers": 1}
    serial = build(tmp_path / "serial")
    kwargs["max_workers"] = 3
    parallel = build(tmp_path / "parallel")

    assert [p.page_index for p in serial.pages] == [p.page_index for p in parallel.pages]
    assert serial.failed_pages == parallel.failed_pages


def test_parallel_marks_all_page_jobs_succeeded(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 3)
    _run(store, project_id, tmp_path, FakeEngine("primary"), FakeEngine("secondary"), max_workers=3)

    for page_index in range(3):
        for stage in (JobStage.PRIMARY_OCR, JobStage.SECONDARY_OCR, JobStage.VERIFY):
            job_id = store.ensure_job(project_id, stage, page_index=page_index)
            assert store.load_job(job_id).state is JobState.SUCCEEDED


def test_parallel_respects_resume_and_skips_finished_pages(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 3)
    primary = FakeEngine("primary", "鸦")
    secondary = FakeEngine("secondary", "雅")

    first = _run(store, project_id, tmp_path, primary, secondary, max_workers=1)
    assert len(first.pages) == 3

    primary.calls.clear()
    resumed = _run(store, project_id, tmp_path, primary, secondary, max_workers=3)
    assert resumed.failed_pages == ()
    # Completed pages are resumed, not re-read by the engines.
    assert primary.calls == []


def test_workers_larger_than_page_count_is_fine(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 2)
    outcome = _run(
        store,
        project_id,
        tmp_path,
        FakeEngine("primary"),
        FakeEngine("secondary"),
        max_workers=16,
    )
    assert [page.page_index for page in outcome.pages] == [0, 1]
    assert outcome.failed_pages == ()


def test_both_engines_are_called_for_every_page_in_parallel(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 3)
    primary = FakeEngine("primary", "鸦")
    secondary = FakeEngine("secondary", "雅")
    _run(store, project_id, tmp_path, primary, secondary, max_workers=3)
    assert sorted(primary.calls) == [0, 1, 2]
    assert sorted(secondary.calls) == [0, 1, 2]


def test_parallel_propagates_job_already_running_error(tmp_path: Path) -> None:
    owner_store, project_id = _store(tmp_path, 1)
    page = owner_store.list_pages(project_id)[0]
    owned_job = owner_store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page.page_index)
    owner_store.start_job(owned_job)

    contender = SQLiteStore(tmp_path / "project.sqlite3")
    contender.initialize()
    with pytest.raises(JobAlreadyRunningError):
        _run(
            contender,
            project_id,
            tmp_path,
            FakeEngine("primary"),
            FakeEngine("secondary"),
            max_workers=2,
        )
    owner_store.fail_job(owned_job, "cleanup after parallel ownership assertion")


def test_resolve_max_workers_defaults_to_half_the_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    from scribeforge.domain.scheduling import resolve_cpu_workers

    monkeypatch.setattr(os, "cpu_count", lambda: 8, raising=False)
    assert resolve_cpu_workers(0) == 4
    assert resolve_cpu_workers(1) == 1
    assert resolve_cpu_workers(7) == 7
    with pytest.raises(ValueError, match="max_workers"):
        resolve_cpu_workers(-1)

    monkeypatch.setattr(os, "cpu_count", lambda: None, raising=False)
    assert resolve_cpu_workers(0) == 1


def test_parallel_mixed_failures_match_serial_classification(tmp_path: Path) -> None:
    def build(root: Path, workers: int):
        store, project_id = _store(root, 4)
        primary = SelectiveFailureEngine("primary", failing_pages={1, 3})
        secondary = FakeEngine("secondary", "雅")
        return _run(store, project_id, root, primary, secondary, max_workers=workers)

    serial = build(tmp_path / "serial", 1)
    parallel = build(tmp_path / "parallel", 4)

    assert [page.page_index for page in serial.pages] == [
        page.page_index for page in parallel.pages
    ]
    assert [index for index, _ in serial.failed_pages] == [
        index for index, _ in parallel.failed_pages
    ]
    assert serial.failed_pages == parallel.failed_pages


def test_repair_round_still_runs_after_parallel_first_pass(tmp_path: Path) -> None:
    from scribeforge.pipeline.rounds import RepairSettings

    store, project_id = _store(tmp_path, 2)
    outcome = process_book(
        store,
        project_id,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        crops_dir=tmp_path / "crops",
        max_workers=2,
        repair=RepairSettings(max_rounds=1, max_pairs_per_round=5),
    )
    # Both pages agree on nothing (鸦 vs 雅) -> conflict pairs exist to repair.
    assert len(outcome.pages) == 2
    assert outcome.failed_pages == ()


def test_cancellation_does_not_wait_for_in_flight_pages(tmp_path: Path) -> None:
    """A slow in-flight page must not delay cancellation (issue #70 regression).

    On multi-core hosts the default worker count is > 1, so pages run on the
    pool; when one page observes cancellation, process_book has to raise
    promptly instead of draining pages whose engine subprocesses still run.
    """
    store, project_id = _store(tmp_path, 3)
    token = CancellationToken()

    class SlowCancellingEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            if page_index == 0:
                # Page 0 cancels immediately; pages 1 and 2 are slow.
                token.cancel()
                token.raise_if_cancelled()
            time.sleep(5)
            return super().analyze_page(image_path, page_index)

    with cancellation_scope(token):
        started = time.monotonic()
        with pytest.raises(ProcessingCancelled):
            _run(
                store,
                project_id,
                tmp_path,
                SlowCancellingEngine("primary"),
                FakeEngine("secondary", "雅"),
                max_workers=3,
            )
        elapsed = time.monotonic() - started

    assert elapsed < 5, f"cancellation waited {elapsed:.1f}s for in-flight pages"


def test_cancel_drains_started_page_before_returning_and_keeps_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce #265: page zero reports cancellation before page one's fail commit."""
    from scribeforge.pipeline import orchestrator

    monkeypatch.setattr(orchestrator, "_CANCELLATION_DRAIN_SECONDS", 10.0)
    store, project_id = _store(tmp_path, 2)
    source_bytes = (tmp_path / "page-0.png").read_bytes()
    token = CancellationToken()
    page0_started = threading.Event()
    cancellation_sent = threading.Event()
    fail_started = threading.Event()
    release_failure = threading.Event()
    completed = threading.Event()
    errors: list[BaseException] = []
    original_fail_job = store.fail_job

    def gated_fail(job_id: int, error: str):
        if store.load_job(job_id).page_index == 1:
            fail_started.set()
            if not release_failure.wait(15):
                raise RuntimeError("test failed to release page failure")
        return original_fail_job(job_id, error)

    monkeypatch.setattr(store, "fail_job", gated_fail)

    class Engine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            if page_index == 0:
                page0_started.set()
                if not cancellation_sent.wait(15):
                    raise RuntimeError("test cancellation not requested")
                token.raise_if_cancelled()
            else:
                if not page0_started.wait(15):
                    raise RuntimeError("first page did not start")
                token.cancel()
                cancellation_sent.set()
                token.raise_if_cancelled()
            raise AssertionError("unreachable")

    def run() -> None:
        try:
            with cancellation_scope(token):
                _run(
                    store,
                    project_id,
                    tmp_path,
                    Engine("primary"),
                    FakeEngine("secondary"),
                    max_workers=2,
                )
        except Exception as exc:  # noqa: BLE001 - retain test thread failures
            errors.append(exc)
        finally:
            completed.set()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert fail_started.wait(15)
        assert not completed.wait(0.2), "caller returned before the active page finalized"
        assert (tmp_path / "page-0.png").read_bytes() == source_bytes
    finally:
        release_failure.set()
        worker.join(20)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], ProcessingCancelled)
    for page in (0, 1):
        job = store.load_job(store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page))
        assert job.state is JobState.FAILED
        assert "cancel" in (job.last_error or "")
    assert (tmp_path / "page-0.png").read_bytes() == source_bytes
    resumed = _run(
        store, project_id, tmp_path, FakeEngine("primary"), FakeEngine("secondary"), max_workers=2
    )
    assert not resumed.failed_pages
    assert len(resumed.pages) == 2


def test_noncooperative_page_reports_pending_and_retains_its_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.pipeline import orchestrator

    monkeypatch.setattr(orchestrator, "_CANCELLATION_DRAIN_SECONDS", 0.1)
    store, project_id = _store(tmp_path, 2)
    token = CancellationToken()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_fail = store.fail_job

    def notified_fail(job_id: int, error: str):
        result = original_fail(job_id, error)
        if result.page_index == 0:
            finished.set()
        return result

    monkeypatch.setattr(store, "fail_job", notified_fail)

    class UncooperativeEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            if page_index == 0:
                started.set()
                if not release.wait(15):
                    raise RuntimeError("test failed to release slow engine")
                return super().analyze_page(image_path, page_index)
            if not started.wait(15):
                raise RuntimeError("slow engine never started")
            token.cancel()
            token.raise_if_cancelled()
            raise AssertionError("unreachable")

    try:
        with (
            cancellation_scope(token),
            pytest.raises(ProcessingCancelled, match="still stopping") as error,
        ):
            _run(
                store,
                project_id,
                tmp_path,
                UncooperativeEngine("primary"),
                FakeEngine("secondary"),
                max_workers=2,
            )
        assert "physical pages [1]" in str(error.value)
        job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
        assert store.load_job(job_id).state is JobState.RUNNING
        other = SQLiteStore(tmp_path / "project.sqlite3")
        with pytest.raises(JobAlreadyRunningError):
            other.start_job(job_id)
    finally:
        release.set()
        assert finished.wait(15)
    assert store.load_job(job_id).state is JobState.FAILED
    with pytest.raises(KeyError):
        store.latest_engine_run(project_id, 0, "primary")
    assert (
        _run(
            store,
            project_id,
            tmp_path,
            FakeEngine("primary"),
            FakeEngine("secondary"),
            max_workers=1,
        ).failed_pages
        == ()
    )


def test_failure_record_write_error_is_reported_without_releasing_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project_id = _store(tmp_path, 2)
    token = CancellationToken()
    both_ready = threading.Barrier(2)
    original_fail = store.fail_job

    def failing_write(job_id: int, error: str):
        if store.load_job(job_id).page_index == 1:
            raise OSError("injected failure persisting job")
        return original_fail(job_id, error)

    monkeypatch.setattr(store, "fail_job", failing_write)

    class BothCancel(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            both_ready.wait(timeout=15)
            token.cancel()
            token.raise_if_cancelled()
            raise AssertionError("unreachable")

    with (
        cancellation_scope(token),
        pytest.raises(ProcessingCancelled, match="reported errors") as exc,
    ):
        _run(
            store,
            project_id,
            tmp_path,
            BothCancel("primary"),
            FakeEngine("secondary"),
            max_workers=2,
        )
    assert "physical pages [2]" in str(exc.value)
    job = store.load_job(store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=1))
    assert job.state is JobState.RUNNING
    with pytest.raises(JobAlreadyRunningError):
        SQLiteStore(tmp_path / "project.sqlite3").start_job(job.id)
    original_fail(job.id, "cleanup after injected failure")
    assert store.load_job(job.id).state is JobState.FAILED


def test_queued_pages_are_not_started_after_cancellation(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 12)
    token = CancellationToken()
    both_ready = threading.Barrier(2)
    called: list[int] = []
    lock = threading.Lock()

    class Engine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            with lock:
                called.append(page_index)
            both_ready.wait(timeout=15)
            token.cancel()
            token.raise_if_cancelled()
            raise AssertionError("unreachable")

    with cancellation_scope(token), pytest.raises(ProcessingCancelled):
        _run(store, project_id, tmp_path, Engine("primary"), FakeEngine("secondary"), max_workers=2)
    assert sorted(called) == [0, 1]
    jobs = store.list_jobs(project_id)
    assert all(job.page_index in (0, 1) for job in jobs)
    assert all(job.state is not JobState.RUNNING for job in jobs)
