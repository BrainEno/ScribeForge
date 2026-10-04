from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.storage.sqlite import SQLiteStore


def _store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    store.add_page(project_id, 0, "pages/000001.png", 1000, 1600)
    store.add_page(project_id, 1, "pages/000002.png", 1000, 1600)
    return store, project_id


def test_schema_v4_contains_resumable_jobs_table(tmp_path: Path) -> None:
    path = tmp_path / "project.sqlite3"
    SQLiteStore(path).initialize()

    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert version == 4
    assert "jobs" in tables


def test_page_job_lifecycle_records_attempts_failure_and_retry(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)

    pending = store.load_job(job_id)
    assert pending.stage is JobStage.PRIMARY_OCR
    assert pending.state is JobState.PENDING
    assert pending.page_index == 0
    assert pending.attempts == 0
    assert pending.last_error is None

    running = store.start_job(job_id)
    assert running.state is JobState.RUNNING
    assert running.attempts == 1

    failed = store.fail_job(job_id, "worker crashed")
    assert failed.state is JobState.FAILED
    assert failed.attempts == 1
    assert failed.last_error == "worker crashed"
    assert [job.id for job in store.list_resumable_jobs(project_id)] == [job_id]

    retried = store.start_job(job_id)
    assert retried.state is JobState.RUNNING
    assert retried.attempts == 2
    assert retried.last_error is None

    completed = store.complete_job(job_id)
    assert completed.state is JobState.SUCCEEDED
    assert completed.attempts == 2
    assert store.list_resumable_jobs(project_id) == ()

    with pytest.raises(ValueError, match="succeeded"):
        store.start_job(job_id)


def test_ensure_job_is_idempotent_per_project_page_and_stage(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    first = store.ensure_job(project_id, JobStage.SECONDARY_OCR, page_index=1)
    second = store.ensure_job(project_id, JobStage.SECONDARY_OCR, page_index=1)
    different_stage = store.ensure_job(project_id, JobStage.VERIFY, page_index=1)

    assert first == second
    assert different_stage != first


def test_running_job_remains_resumable_after_store_reopens(tmp_path: Path) -> None:
    path = tmp_path / "project.sqlite3"
    store, project_id = _store(tmp_path)
    job_id = store.ensure_job(project_id, JobStage.VLM_REVIEW, page_index=0)
    store.start_job(job_id)

    reopened = SQLiteStore(path)
    reopened.initialize()

    jobs = reopened.list_resumable_jobs(project_id)
    assert [(job.id, job.state, job.attempts) for job in jobs] == [
        (job_id, JobState.RUNNING, 1)
    ]


def test_project_import_job_is_idempotent_and_page_stages_require_page_scope(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    first = store.ensure_job(project_id, JobStage.IMPORT)
    second = store.ensure_job(project_id, JobStage.IMPORT)

    assert first == second
    assert store.load_job(first).page_index is None

    with pytest.raises(ValueError, match="page-scoped"):
        store.ensure_job(project_id, JobStage.PRIMARY_OCR)
    with pytest.raises(ValueError, match="project-scoped"):
        store.ensure_job(project_id, JobStage.IMPORT, page_index=0)
    with pytest.raises(KeyError, match="unknown project/page"):
        store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=99)


def test_job_transitions_require_running_state_and_nonblank_error(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    job_id = store.ensure_job(project_id, JobStage.VERIFY, page_index=0)

    with pytest.raises(ValueError, match="running"):
        store.complete_job(job_id)
    with pytest.raises(ValueError, match="running"):
        store.fail_job(job_id, "boom")

    store.start_job(job_id)
    with pytest.raises(ValueError, match="error"):
        store.fail_job(job_id, "   ")


def test_v3_database_migrates_to_v4_without_losing_project(tmp_path: Path) -> None:
    path = tmp_path / "legacy-v3.sqlite3"
    store = SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        store._create_v1(connection)
        store._create_v2(connection)
        store._create_v3(connection)
        connection.execute(
            "INSERT INTO projects(title, source_uri, source_sha256) VALUES (?, ?, ?)",
            ("Legacy", "old.pdf", "oldhash"),
        )
        connection.execute("PRAGMA user_version = 3")

    store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Legacy"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()[0] == 1
