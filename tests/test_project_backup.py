from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

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
    assert project.source_uri == str(source.resolve())
    assert project.source_sha256 == _sha256(source)
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
    with zipfile.ZipFile(archive, "r") as source_zip, zipfile.ZipFile(
        tampered, "w", compression=zipfile.ZIP_DEFLATED
    ) as target_zip:
        for item in source_zip.infolist():
            data = source_zip.read(item.filename)
            if item.filename == "assets/page-000001.png":
                data += b"tampered"
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
