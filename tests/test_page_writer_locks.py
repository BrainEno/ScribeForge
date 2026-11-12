"""Page-granular writer lock tests (issue #70).

The project writer lock used to be project-wide: only one running job per
project per process. Page-level parallel processing needs one running job per
*page*, while same-page and cross-process conflicts must still raise
``JobAlreadyRunningError``. Book-level stages (IMPORT, REPAIR, ...) have no page
and keep the project-wide lock.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.storage.sqlite import JobAlreadyRunningError, SQLiteStore


def _store(tmp_path: Path, name: str = "project.sqlite3") -> SQLiteStore:
    store = SQLiteStore(tmp_path / name)
    store.initialize()
    return store


def _project(store: SQLiteStore) -> int:
    return store.create_project("Book", "book.pdf", "source-hash")


def _add_page(store: SQLiteStore, project_id: int, index: int) -> int:
    store.add_page(project_id, index, f"/tmp/page-{index}.png", 100, 150)
    return store.list_pages(project_id)[-1].page_index


def test_different_pages_can_hold_running_jobs_concurrently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    project_id = _project(store)
    page_a = _add_page(store, project_id, 0)
    page_b = _add_page(store, project_id, 1)

    job_a = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page_a)
    job_b = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page_b)

    store.start_job(job_a)
    store.start_job(job_b)  # must not raise: different page, same store

    assert store.load_job(job_a).state is JobState.RUNNING
    assert store.load_job(job_b).state is JobState.RUNNING
    store.complete_job(job_a)
    store.complete_job(job_b)


def test_same_page_second_job_conflicts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    project_id = _project(store)
    page = _add_page(store, project_id, 0)

    first = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page)
    second = store.ensure_job(project_id, JobStage.SECONDARY_OCR, page_index=page)
    store.start_job(first)

    with pytest.raises(JobAlreadyRunningError):
        store.start_job(second)
    store.complete_job(first)


def test_same_page_conflicts_across_store_instances(tmp_path: Path) -> None:
    first_store = _store(tmp_path)
    project_id = _project(first_store)
    page = _add_page(first_store, project_id, 0)

    second_store = _store(tmp_path)
    job = second_store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page)
    first_store.start_job(job)

    other_job = second_store.ensure_job(project_id, JobStage.SECONDARY_OCR, page_index=page)
    with pytest.raises(JobAlreadyRunningError, match="writer lock"):
        second_store.start_job(other_job)
    first_store.complete_job(job)


def test_book_level_stage_keeps_project_wide_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    project_id = _project(store)
    page = _add_page(store, project_id, 0)

    import_job = store.ensure_job(project_id, JobStage.IMPORT)
    page_job = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page)
    store.start_job(import_job)

    # A page job conflicts with a running book-level job (project-wide lock).
    with pytest.raises(JobAlreadyRunningError):
        store.start_job(page_job)
    store.complete_job(import_job)

    # After the book-level job finishes, the page job can run.
    store.start_job(page_job)
    store.complete_job(page_job)


def test_page_lock_released_after_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    project_id = _project(store)
    page = _add_page(store, project_id, 0)

    job = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page)
    store.start_job(job)
    store.fail_job(job, "engine crashed")

    next_job = store.ensure_job(project_id, JobStage.SECONDARY_OCR, page_index=page)
    store.start_job(next_job)  # lock was released by fail_job
    store.complete_job(next_job)


def test_close_releases_all_page_locks(tmp_path: Path) -> None:
    store = _store(tmp_path)
    project_id = _project(store)
    page = _add_page(store, project_id, 0)
    job = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page)
    store.start_job(job)
    store.close()

    fresh = _store(tmp_path)
    other = fresh.ensure_job(project_id, JobStage.SECONDARY_OCR, page_index=page)
    fresh.start_job(other)  # would raise if the old lock were still held
    fresh.complete_job(other)


def test_many_threads_can_start_and_complete_distinct_page_jobs(tmp_path: Path) -> None:
    import threading

    store = _store(tmp_path)
    project_id = _project(store)
    page_count = 12
    for index in range(page_count):
        _add_page(store, project_id, index)

    errors: list[BaseException] = []

    def work(page_index: int) -> None:
        try:
            for stage in (JobStage.PRIMARY_OCR, JobStage.SECONDARY_OCR, JobStage.VERIFY):
                job_id = store.ensure_job(project_id, stage, page_index=page_index)
                store.start_job(job_id)
                store.complete_job(job_id)
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(page_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    for index in range(page_count):
        for stage in (JobStage.PRIMARY_OCR, JobStage.SECONDARY_OCR, JobStage.VERIFY):
            job_id = store.ensure_job(project_id, stage, page_index=index)
            assert store.load_job(job_id).state is JobState.SUCCEEDED


def test_concurrent_claim_of_the_same_page_yields_exactly_one_winner(tmp_path: Path) -> None:
    import threading

    store = _store(tmp_path)
    project_id = _project(store)
    page = _add_page(store, project_id, 0)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=page)

    outcomes: list[str] = []
    lock = threading.Lock()

    def claim() -> None:
        try:
            store.start_job(job_id)
        except JobAlreadyRunningError:
            with lock:
                outcomes.append("conflict")
        else:
            with lock:
                outcomes.append("winner")

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert outcomes.count("winner") == 1
    assert outcomes.count("conflict") == 7
    store.complete_job(job_id)
