"""Source extent is evidence from verified input, not a guess from imported pages."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.cli import Engines, run
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.source import RenderedPage
from scribeforge.export_readiness import ReadinessOptions, read_export_readiness
from scribeforge.pipeline.import_integrity import require_completed_import
from scribeforge.pipeline.importing import import_source
from scribeforge.storage.sqlite import JobAlreadyRunningError, JobOwnershipError, SQLiteStore


class Source:
    def __init__(self, count: int, fail_at: int | None = None) -> None:
        self.page_count = count
        self.fail_at = fail_at
        self.calls: list[int] = []

    def render_page(self, page_index: int) -> RenderedPage:
        self.calls.append(page_index)
        if page_index == self.fail_at:
            raise RuntimeError("renderer failed")
        output = io.BytesIO()
        Image.new("RGB", (30, 40), "white").save(output, format="PNG")
        return RenderedPage(page_index, 30, 40, output.getvalue(), "png")


class Engine:
    def __init__(self, name: str) -> None:
        self.name = name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        return PageOCRResult(
            page_index, self.name, "fixture", (OCRLine("text", BoundingBox(0, 0, 0.5, 0.1)),)
        )


def factory(runtime_root: Path) -> Engines:
    return Engines(primary=Engine("one"), secondary=Engine("two"), reviewer=None)


def _store(path: Path) -> tuple[SQLiteStore, int, str]:
    store = SQLiteStore(path)
    store.initialize()
    digest = hashlib.sha256(b"book").hexdigest()
    project = store.create_project("book", "book.pdf", digest)
    return store, project, digest


def _downgrade_to_v17(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE source_extent")
        db.execute("PRAGMA user_version = 17")


def test_migrate_v17_leaves_old_extent_unknown_and_preserves_evidence(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    store, project, _ = _store(path)
    store.add_page(project, 0, "original.png", 30, 40)
    before = store.list_pages(project)
    _downgrade_to_v17(path)
    assert SQLiteStore(path, read_only=True).source_page_count(project) is None
    store.initialize()
    store.initialize()
    assert store.source_page_count(project) is None
    assert store.list_pages(project) == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 20
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_failed_v18_migration_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    _store(path)
    _downgrade_to_v17(path)
    with sqlite3.connect(path) as db:

        def deny(action: int, first: str, second: str, database: str, source: str) -> int:
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_PRAGMA and first == "user_version"
                else sqlite3.SQLITE_OK
            )

        db.set_authorizer(deny)
        with pytest.raises(sqlite3.DatabaseError):
            SQLiteStore._migrate_v18(db)
        db.set_authorizer(None)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 17
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='source_extent'").fetchone()
            is None
        )
    SQLiteStore(path).initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 20


def test_interrupted_import_retains_verified_extent_and_resumes(tmp_path: Path) -> None:
    store, project, digest = _store(tmp_path / "db.sqlite3")
    interrupted = Source(4, fail_at=2)
    with pytest.raises(RuntimeError, match="renderer failed"):
        import_source(
            store, project, interrupted, tmp_path / "pages", verified_source_sha256=digest
        )
    assert store.source_page_count(project) == 4
    assert store.imported_page_indexes(project) == frozenset({0, 1})
    job = store.ensure_job(project, JobStage.IMPORT)
    assert store.load_job(job).state is JobState.FAILED
    saved = store.list_pages(project)
    resumed_source = Source(4)
    result = import_source(
        store, project, resumed_source, tmp_path / "pages", verified_source_sha256=digest
    )
    assert resumed_source.calls == [2, 3]
    assert store.list_pages(project)[:2] == saved
    assert store.load_job(result.job_id).attempts == 2
    assert store.source_page_count(project) == 4
    require_completed_import(store, project)


def test_wrong_count_on_resume_refuses_before_render_and_does_not_replace(tmp_path: Path) -> None:
    store, project, digest = _store(tmp_path / "db.sqlite3")
    with pytest.raises(RuntimeError):
        import_source(
            store, project, Source(4, fail_at=1), tmp_path / "pages", verified_source_sha256=digest
        )
    before = store.list_pages(project)
    different = Source(5)
    with pytest.raises(ValueError, match="recorded source extent differs"):
        import_source(store, project, different, tmp_path / "pages", verified_source_sha256=digest)
    assert different.calls == []
    assert store.list_pages(project) == before
    assert store.source_page_count(project) == 4
    assert store.load_job(store.ensure_job(project, JobStage.IMPORT)).state is JobState.FAILED


def test_legacy_backfill_requires_completed_full_import_and_never_guesses(tmp_path: Path) -> None:
    store, project, digest = _store(tmp_path / "db.sqlite3")
    source = Source(2)
    result = import_source(store, project, source, tmp_path / "pages")
    assert store.source_page_count(project) is None
    with pytest.raises(ValueError, match="source page count"):
        store.record_source_page_count(result.job_id, digest, 3)
    assert store.source_page_count(project) is None
    store.record_source_page_count(result.job_id, digest, 2)
    store.record_source_page_count(result.job_id, digest, 2)
    with pytest.raises(ValueError, match="extent differs"):
        store.record_source_page_count(result.job_id, digest, 1)
    assert store.source_page_count(project) == 2


def test_only_owner_can_record_running_import_and_other_job_is_rejected(tmp_path: Path) -> None:
    db = tmp_path / "db.sqlite3"
    store, project, digest = _store(db)
    job = store.ensure_job(project, JobStage.IMPORT)
    with pytest.raises(ValueError, match="running or completed"):
        store.record_source_page_count(job, digest, 3)
    store.start_job(job)
    other = SQLiteStore(db)
    with pytest.raises(JobOwnershipError):
        other.record_source_page_count(job, digest, 3)
    with pytest.raises(ValueError, match="digest does not match"):
        store.record_source_page_count(job, "wrong", 3)
    assert store.source_page_count(project) is None
    store.record_source_page_count(job, digest, 3)
    store.fail_job(job, "cancelled")
    store.add_page(project, 0, "page.png", 30, 40)
    page_job = store.ensure_job(project, JobStage.PRIMARY_OCR, page_index=0)
    with pytest.raises(ValueError, match="requires an import job"):
        store.record_source_page_count(page_job, digest, 3)


def test_completed_backfill_respects_existing_project_lock(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    store, project, digest = _store(path)
    job = import_source(store, project, Source(1), tmp_path / "pages").job_id
    other = SQLiteStore(path)
    with other.project_writer_guard(project), pytest.raises(JobAlreadyRunningError):
        store.record_source_page_count(job, digest, 1)
    assert store.source_page_count(project) is None
    store.record_source_page_count(job, digest, 1)


def test_extent_detects_deleted_tail_and_blocks_candidate_and_readiness(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "project.sqlite3"
    store, project, digest = _store(path)
    import_source(store, project, Source(3), tmp_path / "pages", verified_source_sha256=digest)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM pages WHERE project_id=? AND page_index=2", (project,))
    with pytest.raises(ValueError, match="source has 3 physical pages"):
        require_completed_import(store, project)
    readiness = read_export_readiness(path, ReadinessOptions())
    assert not readiness["ready"]
    assert "source has 3 physical pages" in readiness["issues"][0]["detail"]
    assert (
        run(["export-candidates", "--store", str(tmp_path), "--out-dir", str(tmp_path / "out")])
        == 2
    )
    assert "source has 3 physical pages" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_source_identity_tampering_is_detected(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    store, project, digest = _store(db_path)
    import_source(store, project, Source(1), tmp_path / "pages", verified_source_sha256=digest)
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE projects SET source_sha256='different' WHERE id=?", (project,))
    with pytest.raises(ValueError, match="digest does not match"):
        store.source_page_count(project)
    with pytest.raises(ValueError, match="digest does not match"):
        require_completed_import(store, project)


def test_real_cli_captures_extent_and_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "book.pdf"
    images = [Image.new("RGB", (30, 40), "white") for _ in range(3)]
    images[0].save(source, save_all=True, append_images=images[1:])
    store = tmp_path / "project"
    assert (
        run(
            ["process", str(source), "--store", str(store), "--workers", "1"],
            engine_factory=factory,
        )
        == 0
    )
    capsys.readouterr()
    assert run(["status", "--store", str(store), "--json"]) == 0
    project = json.loads(capsys.readouterr().out)["projects"][0]
    assert project["pages_total"] == 3
    assert project["source_pages_total"] == 3
    assert project["import_state"] == "succeeded"


def test_verified_extent_cannot_be_bypassed_by_unverified_import(tmp_path: Path) -> None:
    store, project, digest = _store(tmp_path / "db.sqlite3")
    with pytest.raises(RuntimeError):
        import_source(
            store, project, Source(2, fail_at=1), tmp_path / "pages", verified_source_sha256=digest
        )
    job = store.load_job(store.ensure_job(project, JobStage.IMPORT))
    with pytest.raises(ValueError, match="requires a verified source snapshot"):
        import_source(store, project, Source(2), tmp_path / "pages")
    assert store.load_job(job.id) == job
    assert store.source_page_count(project) == 2


def test_missing_import_job_is_not_treated_as_legacy_when_extent_exists(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    store, project, digest = _store(path)
    import_source(store, project, Source(1), tmp_path / "pages", verified_source_sha256=digest)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM jobs WHERE project_id=? AND stage='import'", (project,))
    with pytest.raises(ValueError, match="import job is missing"):
        require_completed_import(store, project)


def test_cli_backfills_legacy_extent_after_snapshot_without_rewriting_pages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "book.pdf"
    images = [Image.new("RGB", (30, 40), "white") for _ in range(2)]
    images[0].save(source, save_all=True, append_images=images[1:])
    root = tmp_path / "project"
    args = ["process", str(source), "--store", str(root), "--workers", "1"]
    assert run(args, engine_factory=factory) == 0
    capsys.readouterr()
    path = root / "project.sqlite3"
    store = SQLiteStore(path)
    project = store.list_projects()[0].id
    before_pages = store.list_pages(project)
    before_jobs = store.list_jobs(project)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE source_extent")
        db.execute("PRAGMA user_version = 17")
    assert run(args, engine_factory=factory) == 0
    capsys.readouterr()
    assert store.source_page_count(project) == 2
    assert store.list_pages(project) == before_pages
    assert store.list_jobs(project) == before_jobs


def test_invalid_source_extent_inputs_and_unrelated_project(tmp_path: Path) -> None:
    store, project, digest = _store(tmp_path / "db.sqlite3")
    job = store.ensure_job(project, JobStage.IMPORT)
    store.start_job(job)
    for invalid in (0, -1, True):
        with pytest.raises(ValueError, match="positive integer"):
            store.record_source_page_count(job, digest, invalid)
    with pytest.raises(ValueError, match="digest is required"):
        store.record_source_page_count(job, "", 1)
    with pytest.raises(KeyError):
        store.source_page_count(project + 100)
    assert store.source_page_count(project) is None
    store.fail_job(job, "stopped")


def test_running_import_rejects_preexisting_page_beyond_source(tmp_path: Path) -> None:
    store, project, digest = _store(tmp_path / "db.sqlite3")
    store.add_page(project, 3, "old.png", 30, 40)
    job = store.ensure_job(project, JobStage.IMPORT)
    store.start_job(job)
    with pytest.raises(ValueError, match="exceed source extent"):
        store.record_source_page_count(job, digest, 2)
    assert store.source_page_count(project) is None
    store.fail_job(job, "stopped")


def test_failed_extent_write_rolls_back_and_job_remains_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project, digest = _store(tmp_path / "db.sqlite3")
    original = store.record_source_page_count

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("injected storage failure")

    monkeypatch.setattr(store, "record_source_page_count", fail)
    with pytest.raises(OSError, match="storage failure"):
        import_source(store, project, Source(1), tmp_path / "pages", verified_source_sha256=digest)
    job = store.load_job(store.ensure_job(project, JobStage.IMPORT))
    assert job.state is JobState.FAILED
    assert store.source_page_count(project) is None
    assert store.list_pages(project) == ()
    monkeypatch.setattr(store, "record_source_page_count", original)
    import_source(store, project, Source(1), tmp_path / "pages", verified_source_sha256=digest)
    assert store.source_page_count(project) == 1


def test_backup_and_restore_preserve_source_extent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scribeforge.project_backup import create_project_backup, restore_project_backup

    source = tmp_path / "book.png"
    Image.new("RGB", (30, 40), "white").save(source)
    root = tmp_path / "store"
    assert (
        run(
            ["process", str(source), "--store", str(root), "--workers", "1"], engine_factory=factory
        )
        == 0
    )
    capsys.readouterr()
    database = SQLiteStore(root / "project.sqlite3")
    project = database.list_projects()[0].id
    assert database.source_page_count(project) == 1
    archive = tmp_path / "backup.zip"
    create_project_backup(root, archive)
    restored = tmp_path / "restored"
    restore_project_backup(archive, restored)
    reopened = SQLiteStore(restored / "project.sqlite3")
    assert reopened.source_page_count(project) == 1
    require_completed_import(reopened, project)
