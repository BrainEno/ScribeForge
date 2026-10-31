from __future__ import annotations

import multiprocessing as mp
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.storage.sqlite import SQLiteStore


def _claim_job_and_wait(
    database_path: str,
    job_id: int,
    messages: Any,
    release: Any,
) -> None:
    """Hold a job from a child process until the parent releases or kills it."""

    store = SQLiteStore(Path(database_path))
    store.initialize()
    job = store.start_job(job_id)
    messages.put(("claimed", job.attempts))
    release.wait(30)


def _try_claim_job(
    database_path: str,
    job_id: int,
    messages: Any,
) -> None:
    """Report whether an independent process can claim the same job."""

    store = SQLiteStore(Path(database_path))
    store.initialize()
    try:
        job = store.start_job(job_id)
    except RuntimeError as exc:
        messages.put(("blocked", str(exc)))
    else:
        messages.put(("claimed", job.attempts))


def _store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    store.add_page(project_id, 0, "pages/000001.png", 1000, 1600)
    store.add_page(project_id, 1, "pages/000002.png", 1000, 1600)
    return store, project_id


def test_latest_schema_contains_resumable_jobs_table(tmp_path: Path) -> None:
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

    assert version == 12
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


def test_independent_processes_cannot_claim_the_same_page_job(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    context = mp.get_context("spawn")
    messages = context.Queue()
    release = context.Event()
    owner = context.Process(
        target=_claim_job_and_wait,
        args=(str(tmp_path / "project.sqlite3"), job_id, messages, release),
    )
    owner.start()

    try:
        assert messages.get(timeout=15) == ("claimed", 1)
        competitor = context.Process(
            target=_try_claim_job,
            args=(str(tmp_path / "project.sqlite3"), job_id, messages),
        )
        competitor.start()
        competitor.join(timeout=15)
        assert not competitor.is_alive()
        outcome, detail = messages.get(timeout=5)

        assert outcome == "blocked"
        assert any(word in detail.lower() for word in ("running", "owner", "owns"))
        assert store.load_job(job_id).attempts == 1
        assert store.load_job(job_id).state is JobState.RUNNING
    finally:
        release.set()
        owner.join(timeout=15)
        if owner.is_alive():
            owner.terminate()
            owner.join(timeout=5)
        messages.close()
        messages.join_thread()


def test_abnormal_process_exit_allows_safe_running_job_takeover(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    context = mp.get_context("spawn")
    messages = context.Queue()
    release = context.Event()
    owner = context.Process(
        target=_claim_job_and_wait,
        args=(str(tmp_path / "project.sqlite3"), job_id, messages, release),
    )
    owner.start()
    try:
        assert messages.get(timeout=15) == ("claimed", 1)

        # Simulate an OS-level crash: no fail/complete transition gets written,
        # but the OS must drop the process lock as it closes the process.
        owner.terminate()
        owner.join(timeout=15)
        assert not owner.is_alive()

        recovered = store.start_job(job_id)
        assert recovered.state is JobState.RUNNING
        assert recovered.attempts == 2
        assert store.complete_job(job_id).attempts == 2
    finally:
        if owner.is_alive():
            owner.terminate()
            owner.join(timeout=5)
        messages.close()
        messages.join_thread()


def test_only_the_process_that_started_a_job_may_finish_it(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    store.start_job(job_id)

    non_owner = SQLiteStore(tmp_path / "project.sqlite3")
    non_owner.initialize()
    with pytest.raises(RuntimeError, match="owner|owned|own"):
        non_owner.complete_job(job_id)
    with pytest.raises(RuntimeError, match="owner|owned|own"):
        non_owner.fail_job(job_id, "not the owner")

    assert store.load_job(job_id).state is JobState.RUNNING
    assert store.load_job(job_id).attempts == 1
    store.fail_job(job_id, "owner completed failure transition")


def test_same_store_cannot_restart_a_running_job(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)

    store.start_job(job_id)
    with pytest.raises(RuntimeError, match="running|owns"):
        store.start_job(job_id)

    assert store.load_job(job_id).attempts == 1
    store.fail_job(job_id, "cleanup after duplicate-start assertion")


def test_writer_lock_is_scoped_to_a_project_not_the_whole_database(
    tmp_path: Path,
) -> None:
    store, first_project_id = _store(tmp_path)
    second_project_id = store.create_project("Second book", "second.pdf", "second-hash")
    store.add_page(second_project_id, 0, "second/000001.png", 1000, 1600)
    first_job = store.ensure_job(
        first_project_id, JobStage.PRIMARY_OCR, page_index=0
    )
    second_job = store.ensure_job(
        second_project_id, JobStage.PRIMARY_OCR, page_index=0
    )

    store.start_job(first_job)
    store.start_job(second_job)
    assert store.complete_job(first_job).state is JobState.SUCCEEDED
    assert store.load_job(second_job).state is JobState.RUNNING
    store.complete_job(second_job)


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


def test_v3_database_migrates_to_latest_without_losing_project(tmp_path: Path) -> None:
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        assert connection.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Legacy"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()[0] == 1


def test_v4_database_migrates_to_latest_keeping_jobs_and_adding_preprocess(
    tmp_path: Path,
) -> None:
    """The v5 rebuild must preserve job rows, ids and uniqueness.

    SQLite cannot alter a CHECK constraint, so v5 recreates ``jobs``. Getting the
    copy wrong would silently lose resumable work, so the rows are asserted
    field by field rather than only counted.
    """

    path = tmp_path / "legacy-v4.sqlite3"
    store = SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        store._create_v1(connection)
        store._create_v2(connection)
        store._create_v3(connection)
        store._create_v4(connection)
        connection.execute(
            "INSERT INTO projects(title, source_uri, source_sha256) VALUES (?, ?, ?)",
            ("Legacy", "old.pdf", "oldhash"),
        )
        connection.execute(
            """
            INSERT INTO pages(project_id, page_index, source_image, width, height)
            VALUES (1, 0, 'page-00000.png', 100, 200)
            """
        )
        connection.execute(
            """
            INSERT INTO jobs(project_id, page_id, stage, state, attempts, last_error)
            VALUES (1, 1, 'primary_ocr', 'failed', 2, 'engine crashed')
            """
        )
        connection.execute("PRAGMA user_version = 4")

    store.initialize()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        row = connection.execute(
            "SELECT id, project_id, page_id, stage, state, attempts, last_error FROM jobs"
        ).fetchone()
        assert row["id"] == 1
        assert row["stage"] == "primary_ocr"
        assert row["state"] == "failed"
        assert row["attempts"] == 2
        assert row["last_error"] == "engine crashed"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='page_preprocess'"
        ).fetchone()[0] == 1
        # The scope-uniqueness index must survive the rebuild.
        assert connection.execute(
            """
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='index' AND name='uq_jobs_scope_stage'
            """
        ).fetchone()[0] == 1


def test_v5_database_migrates_to_v6_adding_structure_tables(tmp_path: Path) -> None:
    """v6 adds page roles, chapter rows and persisted OCR blocks."""

    path = tmp_path / "legacy-v5.sqlite3"
    store = SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        store._create_v1(connection)
        store._create_v2(connection)
        store._create_v3(connection)
        store._create_v4(connection)
        store._create_v5(connection)
        connection.execute(
            "INSERT INTO projects(title, source_uri, source_sha256) VALUES (?, ?, ?)",
            ("Legacy", "old.pdf", "oldhash"),
        )
        connection.execute("PRAGMA user_version = 5")

    store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"page_structure", "chapters", "ocr_blocks"} <= names
        # Direction and confidence arrive as columns on existing tables.
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(engine_runs)")
        }
        line_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ocr_lines)")
        }
        assert "direction" in run_columns
        assert "confidence" in line_columns


def test_v5_accepts_the_preprocess_stage(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    job_id = store.ensure_job(project_id, JobStage.PREPROCESS, page_index=0)
    store.start_job(job_id)
    completed = store.complete_job(job_id)

    assert completed.stage is JobStage.PREPROCESS
    assert completed.state.value == "succeeded"


def test_preprocess_runs_before_ocr_in_stage_order() -> None:
    from scribeforge.domain.jobs import stage_before

    assert stage_before(JobStage.PREPROCESS, JobStage.PRIMARY_OCR)
    assert JobStage.PREPROCESS.page_scoped
    assert not JobStage.IMPORT.page_scoped


def test_reopen_job_returns_a_succeeded_stage_to_pending(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    store.start_job(job_id)
    store.complete_job(job_id)

    reopened = store.reopen_job(job_id, "asset deleted")

    assert reopened.state.value == "pending"
    assert reopened.last_error == "asset deleted"
    with pytest.raises(ValueError, match="only a succeeded job"):
        store.reopen_job(job_id, "again")
