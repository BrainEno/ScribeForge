from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import zipfile
from pathlib import Path

import pytest

from scribeforge import project_backup
from scribeforge.cli import run
from scribeforge.domain.jobs import JobStage
from scribeforge.project_backup import (
    ProjectBackupError,
    create_project_backup,
    restore_project_backup,
    verify_project_backup,
)
from scribeforge.storage.sqlite import SQLiteStore


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prepared_store(tmp_path: Path) -> tuple[Path, Path]:
    store_dir = tmp_path / "Book.scribeforge"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()

    source = tmp_path / "Book.pdf"
    source.write_bytes(b"stable source PDF bytes")
    project_id = store.create_project("Book", str(source.resolve()), _sha256(source))

    assets = store_dir / "assets"
    assets.mkdir(parents=True)
    (assets / "page-000001.png").write_bytes(b"page raster bytes")
    store.add_page(
        project_id,
        0,
        str((assets / "page-000001.png").resolve()),
        100,
        200,
    )

    crops = store_dir / "crops"
    crops.mkdir()
    (crops / "review-crop.png").write_bytes(b"review crop bytes")
    return store_dir, source


def test_page_asset_relocation_closes_its_sqlite_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "project.sqlite3"
    original_connect = sqlite3.connect
    connection = original_connect(database)
    try:
        connection.execute("CREATE TABLE pages (id INTEGER PRIMARY KEY, source_image TEXT)")
        connection.execute(
            "INSERT INTO pages (id, source_image) VALUES (1, ?)",
            (str(tmp_path / "old" / "assets" / "page.png"),),
        )
        connection.commit()
    finally:
        connection.close()

    opened: list[sqlite3.Connection] = []

    def tracked_connect(*args, **kwargs) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(project_backup.sqlite3, "connect", tracked_connect)
    project_backup._remap_page_assets(database, tmp_path, {"assets/page.png"})

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        opened[0].execute("SELECT 1")


def test_backup_verification_and_restore_preserve_evidence_and_relocate_page_paths(
    tmp_path: Path,
) -> None:
    store_dir, source = _prepared_store(tmp_path)
    archive = tmp_path / "backups" / "Book.zip"

    created = create_project_backup(store_dir, archive)
    verified = verify_project_backup(archive)

    assert created["file_count"] == 4
    assert verified["healthy"] is True
    assert verified["sqlite_integrity"] == "ok"
    assert verified["foreign_key_violations"] == 0

    target = tmp_path / "restored.scribeforge"
    restored = restore_project_backup(archive, target)
    assert restored["healthy"] is True

    store = SQLiteStore(target / "project.sqlite3")
    project = store.list_projects()[0]
    pages = store.list_pages(project.id)
    assert project.title == "Book"
    restored_source = target / "source" / "project-1" / "Book.pdf"
    assert project.source_uri == str(restored_source.resolve())
    assert project.source_uri != str(source.resolve())
    assert project.source_sha256 == _sha256(restored_source)
    assert pages[0].source_image == str((target / "assets" / "page-000001.png").resolve())
    assert (target / "crops" / "review-crop.png").read_bytes() == b"review crop bytes"


def test_backup_refuses_to_overwrite_existing_archive_without_explicit_flag(
    tmp_path: Path,
) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    archive = tmp_path / "Book.zip"
    create_project_backup(store_dir, archive)

    with pytest.raises(FileExistsError):
        create_project_backup(store_dir, archive)

    create_project_backup(store_dir, archive, overwrite=True)
    assert verify_project_backup(archive)["healthy"] is True


def test_backup_archive_inside_store_is_rejected(tmp_path: Path) -> None:
    store_dir, _source = _prepared_store(tmp_path)

    with pytest.raises(ProjectBackupError, match="outside"):
        create_project_backup(store_dir, store_dir / "backup.zip")


def test_backup_rejects_missing_page_asset_instead_of_creating_incomplete_archive(
    tmp_path: Path,
) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    (store_dir / "assets" / "page-000001.png").unlink()

    with pytest.raises(ProjectBackupError, match="page_asset_missing"):
        create_project_backup(store_dir, tmp_path / "Book.zip")


def test_backup_refuses_to_snapshot_while_a_job_is_running(tmp_path: Path) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    store = SQLiteStore(store_dir / "project.sqlite3")
    project_id = store.list_projects()[0].id
    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    store.start_job(job_id)

    with pytest.raises(ProjectBackupError, match="running job"):
        create_project_backup(store_dir, tmp_path / "Book.zip")


def test_verification_detects_tampered_member_bytes(tmp_path: Path) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    archive = tmp_path / "Book.zip"
    create_project_backup(store_dir, archive)

    tampered = tmp_path / "tampered.zip"
    with (
        zipfile.ZipFile(archive, "r") as source_zip,
        zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as target_zip,
    ):
        for item in source_zip.infolist():
            data = source_zip.read(item.filename)
            if item.filename == "assets/page-000001.png":
                data = bytes([data[0] ^ 1]) + data[1:]
            target_zip.writestr(item.filename, data)

    with pytest.raises(ProjectBackupError, match="SHA-256 mismatch"):
        verify_project_backup(tampered)


def test_verification_rejects_path_traversal_archive_entries(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.zip"
    manifest = {
        "format": "scribeforge-project-backup",
        "schema_version": 1,
        "created_at": "2026-10-22T00:00:00Z",
        "source_store": "/old/store",
        "files": [{"path": "../escape.txt", "size": 4, "sha256": _digest(b"evil")}],
    }
    with zipfile.ZipFile(archive, "w") as archive_file:
        archive_file.writestr("manifest.json", json.dumps(manifest))
        archive_file.writestr("../escape.txt", b"evil")

    with pytest.raises(ProjectBackupError, match="unsafe archive path"):
        verify_project_backup(archive)


def test_restore_refuses_to_replace_an_existing_directory(tmp_path: Path) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    archive = tmp_path / "Book.zip"
    create_project_backup(store_dir, archive)
    target = tmp_path / "already-there"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        restore_project_backup(archive, target)

    assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_cli_backup_verify_restore_json_round_trip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    archive = tmp_path / "Book.zip"
    target = tmp_path / "restored-from-cli.scribeforge"

    backup_code = run(["backup", "--store", str(store_dir), "--out", str(archive), "--json"])
    backup_payload = json.loads(capsys.readouterr().out)
    assert backup_code == 0
    assert backup_payload["healthy"] is True

    verify_code = run(["verify-backup", "--backup", str(archive), "--json"])
    verify_payload = json.loads(capsys.readouterr().out)
    assert verify_code == 0
    assert verify_payload["sqlite_integrity"] == "ok"

    restore_code = run(["restore", "--backup", str(archive), "--to", str(target), "--json"])
    restore_payload = json.loads(capsys.readouterr().out)
    assert restore_code == 0
    assert restore_payload["healthy"] is True
    restored_project = SQLiteStore(target / "project.sqlite3").list_projects()[0]
    assert restored_project.source_uri == str(
        (target / "source" / "project-1" / "Book.pdf").resolve()
    )


def test_cli_backup_refuses_overwrite_and_restore_never_replaces_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    archive = tmp_path / "Book.zip"

    assert run(["backup", "--store", str(store_dir), "--out", str(archive)]) == 0
    capsys.readouterr()

    code = run(["backup", "--store", str(store_dir), "--out", str(archive)])
    captured = capsys.readouterr()
    assert code == 2
    assert "already exists" in captured.err

    target = tmp_path / "keep-existing"
    target.mkdir()
    (target / "marker.txt").write_text("preserve", encoding="utf-8")
    restore_code = run(["restore", "--backup", str(archive), "--to", str(target)])
    restore_output = capsys.readouterr()
    assert restore_code == 2
    assert "already exists" in restore_output.err
    assert (target / "marker.txt").read_text(encoding="utf-8") == "preserve"


def test_backup_refuses_to_package_a_source_whose_hash_changed(tmp_path: Path) -> None:
    store_dir, source = _prepared_store(tmp_path)
    source.write_bytes(b"modified source PDF bytes")

    with pytest.raises(ProjectBackupError, match="source SHA-256 mismatch"):
        create_project_backup(store_dir, tmp_path / "Book.zip")


def test_verification_rejects_manifest_that_omits_a_referenced_page_asset(
    tmp_path: Path,
) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    archive = tmp_path / "Book.zip"
    create_project_backup(store_dir, archive)

    incomplete = tmp_path / "incomplete.zip"
    with (
        zipfile.ZipFile(archive, "r") as source_zip,
        zipfile.ZipFile(incomplete, "w", compression=zipfile.ZIP_DEFLATED) as target_zip,
    ):
        manifest = json.loads(source_zip.read("manifest.json"))
        manifest["files"] = [
            record for record in manifest["files"] if record["path"] != "assets/page-000001.png"
        ]
        for item in source_zip.infolist():
            if item.filename == "assets/page-000001.png":
                continue
            if item.filename == "manifest.json":
                target_zip.writestr(item.filename, json.dumps(manifest))
            else:
                target_zip.writestr(item.filename, source_zip.read(item.filename))

    with pytest.raises(ProjectBackupError, match="page image referenced"):
        verify_project_backup(incomplete)


@pytest.mark.parametrize(
    "members",
    [
        ("assets/Page.png", "assets/page.png"),
        ("assets/café.txt", "assets/cafe\u0301.txt"),
        ("assets", "assets/page.png"),
    ],
)
def test_verification_rejects_portable_archive_path_collisions(
    tmp_path: Path, members: tuple[str, str]
) -> None:
    archive = tmp_path / "colliding-paths.zip"
    manifest = {
        "format": "scribeforge-project-backup",
        "schema_version": 1,
        "created_at": "2026-10-23T00:00:00Z",
        "source_store": "/old/store",
        "files": [],
        "sources": [],
    }
    with zipfile.ZipFile(archive, "w") as archive_file:
        archive_file.writestr("manifest.json", json.dumps(manifest))
        for member in members:
            archive_file.writestr(member, b"content")

    with pytest.raises(ProjectBackupError, match="colliding archive paths"):
        verify_project_backup(archive)


@pytest.mark.parametrize("member", ["assets/page.txt:stream", "assets/trailing./page.txt"])
def test_verification_rejects_nonportable_archive_path_components(
    tmp_path: Path, member: str
) -> None:
    archive = tmp_path / "nonportable-path.zip"
    with zipfile.ZipFile(archive, "w") as archive_file:
        archive_file.writestr("manifest.json", "{}")
        archive_file.writestr(member, b"content")

    with pytest.raises(ProjectBackupError, match="unsafe archive path"):
        verify_project_backup(archive)


def test_restore_relocates_preprocessed_page_paths(tmp_path: Path) -> None:
    database = tmp_path / "project.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE pages (id INTEGER PRIMARY KEY, source_image TEXT)")
        connection.execute(
            "CREATE TABLE page_preprocess (page_id INTEGER PRIMARY KEY, output_image TEXT)"
        )
        connection.execute(
            "INSERT INTO pages VALUES (1, ?)", (str(tmp_path / "old" / "assets" / "page.png"),)
        )
        connection.execute(
            "INSERT INTO page_preprocess VALUES (1, ?)",
            (str(tmp_path / "old" / "preprocessed" / "page-pre.png"),),
        )
    target = tmp_path / "restored"
    project_backup._remap_page_assets(
        database, target, {"assets/page.png", "preprocessed/page-pre.png"}
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT output_image FROM page_preprocess").fetchone()[0] == str(
            (target / "preprocessed" / "page-pre.png").resolve()
        )


def test_backup_rejects_job_started_after_live_check_but_before_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must inspect the same SQLite state that is actually archived."""
    store_dir, _source = _prepared_store(tmp_path)
    store = SQLiteStore(store_dir / "project.sqlite3")
    project_id = store.list_projects()[0].id
    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    original_connect = sqlite3.connect
    started: list[bool] = []

    class StartJobBeforeBackup:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def backup(self, destination):
            # Simulate a non-cooperating writer that bypasses the project OS lock.
            from contextlib import closing

            with closing(original_connect(store_dir / "project.sqlite3")) as db, db:
                db.execute("UPDATE jobs SET state='running' WHERE id=?", (job_id,))
            started.append(True)
            return self.connection.backup(destination)

    def tracked_connect(database, *args, **kwargs):
        connection = original_connect(database, *args, **kwargs)
        if Path(database) == store_dir / "project.sqlite3" and kwargs.get("timeout") == 10:
            return StartJobBeforeBackup(connection)
        return connection

    monkeypatch.setattr(project_backup.sqlite3, "connect", tracked_connect)
    archive = tmp_path / "Book.zip"
    archive.write_bytes(b"prior archive")
    try:
        with pytest.raises(ProjectBackupError, match="running job"):
            create_project_backup(store_dir, archive, overwrite=True)
        assert started
        assert archive.read_bytes() == b"prior archive"
        assert not list(tmp_path.glob(".Book.zip.*.tmp"))
    finally:
        if started:
            from contextlib import closing

            with closing(original_connect(store_dir / "project.sqlite3")) as db, db:
                db.execute("UPDATE jobs SET state='failed' WHERE id=?", (job_id,))
    monkeypatch.undo()
    create_project_backup(store_dir, archive, overwrite=True)
    assert verify_project_backup(archive)["healthy"] is True


@pytest.mark.parametrize("phase", ["audit", "snapshot", "archive", "validate"])
def test_backup_excludes_job_claims_through_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from scribeforge.storage.sqlite import JobAlreadyRunningError

    store_dir, _source = _prepared_store(tmp_path)
    other = SQLiteStore(store_dir / "project.sqlite3")
    project_id = other.list_projects()[0].id
    job_id = other.ensure_job(project_id, JobStage.IMPORT)
    names = {
        "audit": "audit_project_store",
        "snapshot": "_snapshot_database",
        "archive": "_stream_file_to_zip",
        "validate": "restore_project_backup",
    }
    name = names[phase]
    original = getattr(project_backup, name)
    blocked = []

    def attempt_during_backup(*args, **kwargs):
        if not blocked:
            try:
                with pytest.raises(JobAlreadyRunningError):
                    other.start_job(job_id)
                blocked.append(True)
            finally:
                other.close()
        return original(*args, **kwargs)

    monkeypatch.setattr(project_backup, name, attempt_during_backup)
    create_project_backup(store_dir, tmp_path / "Book.zip")
    assert blocked
    assert other.load_job(job_id).attempts == 0
    other.start_job(job_id)
    other.fail_job(job_id, "test cleanup")


def test_backup_releases_project_locks_when_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    other = SQLiteStore(store_dir / "project.sqlite3")
    job_id = other.ensure_job(other.list_projects()[0].id, JobStage.IMPORT)
    archive = tmp_path / "Book.zip"
    archive.write_bytes(b"previous archive")

    def fail_validation(*args, **kwargs):
        raise ProjectBackupError("validation failed")

    monkeypatch.setattr(project_backup, "restore_project_backup", fail_validation)
    with pytest.raises(ProjectBackupError, match="validation failed"):
        create_project_backup(store_dir, archive, overwrite=True)
    assert archive.read_bytes() == b"previous archive"
    other.start_job(job_id)
    other.fail_job(job_id, "test cleanup")


def test_backup_refuses_owned_writer_and_preserves_archive(tmp_path: Path) -> None:
    store_dir, _source = _prepared_store(tmp_path)
    other = SQLiteStore(store_dir / "project.sqlite3")
    project_id = other.list_projects()[0].id
    archive = tmp_path / "Book.zip"
    archive.write_bytes(b"previous archive")
    with (
        other.project_writer_guard(project_id),
        pytest.raises(ProjectBackupError, match="active writer"),
    ):
        create_project_backup(store_dir, archive, overwrite=True)
    assert archive.read_bytes() == b"previous archive"
    create_project_backup(store_dir, archive, overwrite=True)
    assert verify_project_backup(archive)["healthy"] is True


def test_backup_releases_earlier_project_locks_if_another_is_busy(tmp_path: Path) -> None:
    store_dir, source = _prepared_store(tmp_path)
    other = SQLiteStore(store_dir / "project.sqlite3")
    first = other.list_projects()[0].id
    second = other.create_project("Second", str(source), _sha256(source))
    job_id = other.ensure_job(first, JobStage.IMPORT)
    with other.project_writer_guard(second):
        with pytest.raises(ProjectBackupError, match="active writer"):
            create_project_backup(store_dir, tmp_path / "backup.zip")
        other.start_job(job_id)
        other.fail_job(job_id, "test cleanup")
    assert not (tmp_path / "backup.zip").exists()


def test_backup_lock_blocks_a_separate_worker_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import sys

    store_dir, _source = _prepared_store(tmp_path)
    other = SQLiteStore(store_dir / "project.sqlite3")
    job_id = other.ensure_job(other.list_projects()[0].id, JobStage.IMPORT)
    original_snapshot = project_backup._snapshot_database
    outcomes: list[str] = []
    script = """
import sys
from pathlib import Path
from scribeforge.storage.sqlite import SQLiteStore, JobAlreadyRunningError
store = SQLiteStore(Path(sys.argv[1]))
try:
    store.start_job(int(sys.argv[2]))
except JobAlreadyRunningError:
    print('blocked')
else:
    store.fail_job(int(sys.argv[2]), 'unexpected lock acquisition')
    print('acquired')
finally:
    store.close()
"""

    def snapshot_with_worker(*args, **kwargs):
        result = subprocess.run(
            [sys.executable, "-c", script, str(store_dir / "project.sqlite3"), str(job_id)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        outcomes.append(result.stdout.strip())
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(project_backup, "_snapshot_database", snapshot_with_worker)
    create_project_backup(store_dir, tmp_path / "backup.zip")
    assert outcomes == ["blocked"]
    assert other.load_job(job_id).attempts == 0
    other.start_job(job_id)
    other.fail_job(job_id, "test cleanup")


def _rewrite_archive(
    archive: Path,
    output: Path,
    *,
    manifest: bytes | None = None,
    drop: tuple[str, ...] = (),
    extra_entries: tuple[tuple[str, bytes, int], ...] = (),
    duplicate: str | None = None,
) -> None:
    with zipfile.ZipFile(archive, "r") as source:
        members = {info.filename: source.read(info.filename) for info in source.infolist()}
    if manifest is not None:
        members["manifest.json"] = manifest
    for name in drop:
        members.pop(name, None)
    with zipfile.ZipFile(output, "w") as target:
        for name, data in members.items():
            target.writestr(name, data)
        for name, data, mode in extra_entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            target.writestr(info, data)
        if duplicate is not None:
            target.writestr(duplicate, members[duplicate])


def _manifest_of(archive: Path) -> dict:
    with zipfile.ZipFile(archive, "r") as source:
        return json.loads(source.read("manifest.json"))


def _valid_archive(tmp_path: Path) -> Path:
    store_dir, _ = _prepared_store(tmp_path)
    archive = tmp_path / "backups" / "Book.zip"
    create_project_backup(store_dir, archive)
    return archive


def test_verification_rejects_missing_archive_file(tmp_path: Path) -> None:
    with pytest.raises(ProjectBackupError, match="backup archive not found"):
        verify_project_backup(tmp_path / "nope.zip")


def test_verification_rejects_directory_entries(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    forged = tmp_path / "forged.zip"
    _rewrite_archive(
        archive,
        forged,
        extra_entries=(("assets/", b"", 0o40755),),
    )
    with pytest.raises(ProjectBackupError, match="must not contain directory entries"):
        verify_project_backup(forged)


def test_verification_rejects_duplicate_member_names(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    forged = tmp_path / "forged.zip"
    _rewrite_archive(archive, forged, duplicate="assets/page-000001.png")
    with pytest.raises(ProjectBackupError, match="duplicate paths"):
        verify_project_backup(forged)


def test_verification_rejects_symlink_entries(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    forged = tmp_path / "forged.zip"
    _rewrite_archive(
        archive,
        forged,
        extra_entries=(("assets/link.png", b"target", stat.S_IFLNK | 0o777),),
    )
    with pytest.raises(ProjectBackupError, match="symlink"):
        verify_project_backup(forged)


def test_verification_rejects_missing_manifest(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    forged = tmp_path / "forged.zip"
    _rewrite_archive(archive, forged, drop=("manifest.json",))
    with pytest.raises(ProjectBackupError, match="manifest is missing"):
        verify_project_backup(forged)


def test_verification_rejects_invalid_json_manifest(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    forged = tmp_path / "forged.zip"
    _rewrite_archive(archive, forged, manifest=b"{not json")
    with pytest.raises(ProjectBackupError, match="manifest is invalid"):
        verify_project_backup(forged)


def test_verification_rejects_unsupported_manifest_format(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    manifest = _manifest_of(archive)
    manifest["format"] = "someone-elses-backup"
    forged = tmp_path / "forged.zip"
    _rewrite_archive(archive, forged, manifest=json.dumps(manifest).encode())
    with pytest.raises(ProjectBackupError, match="unsupported or invalid"):
        verify_project_backup(forged)


def test_verification_rejects_members_missing_from_manifest(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    forged = tmp_path / "forged.zip"
    _rewrite_archive(archive, forged, drop=("crops/review-crop.png",))
    with pytest.raises(ProjectBackupError, match="do not match the backup manifest"):
        verify_project_backup(forged)


def test_verification_rejects_manifest_without_project_database(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    manifest = _manifest_of(archive)
    manifest["files"] = [f for f in manifest["files"] if f["path"] != "project.sqlite3"]
    forged = tmp_path / "forged.zip"
    _rewrite_archive(
        archive,
        forged,
        manifest=json.dumps(manifest).encode(),
        drop=("project.sqlite3",),
    )
    with pytest.raises(ProjectBackupError, match="project.sqlite3"):
        verify_project_backup(forged)


def test_verification_rejects_source_record_hash_mismatch(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    manifest = _manifest_of(archive)
    manifest["sources"][0]["sha256"] = "0" * 64
    forged = tmp_path / "forged.zip"
    _rewrite_archive(archive, forged, manifest=json.dumps(manifest).encode())
    with pytest.raises(ProjectBackupError, match="does not match archived bytes"):
        verify_project_backup(forged)


def test_verification_rejects_manifest_size_mismatch(tmp_path: Path) -> None:
    archive = _valid_archive(tmp_path)
    manifest = _manifest_of(archive)
    for record in manifest["files"]:
        if record["path"] == "assets/page-000001.png":
            record["size"] = record["size"] + 1
    forged = tmp_path / "forged.zip"
    _rewrite_archive(archive, forged, manifest=json.dumps(manifest).encode())
    with pytest.raises(ProjectBackupError, match="size mismatch"):
        verify_project_backup(forged)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda m: m.update(files=[]), "invalid file count"),
        (lambda m: m.update(files=["not-a-dict"]), "invalid file record"),
        (
            lambda m: m["files"][0].update(path=42),
            "non-string file path",
        ),
        (
            lambda m: m["files"][0].update(path="manifest.json"),
            "cannot list itself",
        ),
        (
            lambda m: m["files"][0].update(size=-1),
            "invalid file size",
        ),
        (
            lambda m: m["files"][0].update(sha256="xyz"),
            "invalid SHA-256",
        ),
        (
            lambda m: m.update(files=m["files"] + [m["files"][0]]),
            "duplicate file paths",
        ),
        (
            lambda m: m["sources"][0].update(project_id=0),
            "invalid source record",
        ),
    ],
)
def test_verification_rejects_malformed_manifest_records(
    tmp_path: Path, mutation, match: str
) -> None:
    archive = _valid_archive(tmp_path)
    manifest = _manifest_of(archive)
    mutation(manifest)
    forged = tmp_path / "forged.zip"
    _rewrite_archive(archive, forged, manifest=json.dumps(manifest).encode())
    with pytest.raises(ProjectBackupError, match=match):
        verify_project_backup(forged)


def test_remap_project_sources_rejects_hash_mismatch(tmp_path: Path) -> None:
    database = tmp_path / "project.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, source_uri TEXT, source_sha256 TEXT)"
        )
        connection.execute("INSERT INTO projects VALUES (1, '/old/Book.pdf', ?)", ("a" * 64,))
    with pytest.raises(ProjectBackupError, match="does not match a restored project"):
        project_backup._remap_project_sources(
            database,
            tmp_path / "new",
            [
                {
                    "archive_path": "source/project-1/Book.pdf",
                    "project_id": 1,
                    "sha256": "b" * 64,
                }
            ],
        )


def test_validate_sqlite_database_rejects_non_project_store(tmp_path: Path) -> None:
    database = tmp_path / "plain.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    with pytest.raises(ProjectBackupError, match="not a ScribeForge project store"):
        project_backup._validate_sqlite_database(database)


def test_remap_page_assets_relocates_crop_evidence_paths(tmp_path: Path) -> None:
    database = tmp_path / "project.sqlite3"
    old_root = tmp_path / "old"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE pages (id INTEGER PRIMARY KEY, source_image TEXT)")
        connection.execute("CREATE TABLE vlm_reviews (id INTEGER PRIMARY KEY, crop_path TEXT)")
        connection.execute(
            "CREATE TABLE crop_input_evidence (id INTEGER PRIMARY KEY, crop_path TEXT)"
        )
        connection.execute(
            "INSERT INTO vlm_reviews VALUES (1, ?)",
            (str(old_root / "crops" / "c1.png"),),
        )
        connection.execute(
            "INSERT INTO crop_input_evidence VALUES (1, ?)",
            (str(old_root / "crops" / "c2.png"),),
        )
    project_backup._remap_page_assets(database, tmp_path / "new", {"crops/c1.png", "crops/c2.png"})
    with sqlite3.connect(database) as connection:
        vlm_path = connection.execute("SELECT crop_path FROM vlm_reviews WHERE id = 1").fetchone()
        crop_path = connection.execute(
            "SELECT crop_path FROM crop_input_evidence WHERE id = 1"
        ).fetchone()
    assert vlm_path == (str((tmp_path / "new" / "crops" / "c1.png").resolve()),)
    assert crop_path == (str((tmp_path / "new" / "crops" / "c2.png").resolve()),)
