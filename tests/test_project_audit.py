from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scribeforge.cli import run
from scribeforge.domain.page_asset import PageAssetEvidence
from scribeforge.project_audit import audit_project_store
from scribeforge.storage.sqlite import SQLiteStore


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _store_with_source(tmp_path: Path) -> tuple[Path, Path]:
    store_dir = tmp_path / "Book.scribeforge"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()

    source = tmp_path / "Book.pdf"
    source.write_bytes(b"sample source PDF bytes")
    project_id = store.create_project("Book", str(source.resolve()), _sha256(source))

    assets = store_dir / "assets"
    assets.mkdir(parents=True)
    page_image = assets / "page-000001.png"
    page_image_bytes = b"sample page image bytes"
    page_image.write_bytes(page_image_bytes)
    store.add_page(
        project_id,
        0,
        str(page_image.resolve()),
        100,
        200,
        page_asset=PageAssetEvidence.from_bytes(page_image_bytes),
    )
    return store_dir, source


def test_project_audit_reports_healthy_store_without_mutating_it(tmp_path: Path) -> None:
    store_dir, _source = _store_with_source(tmp_path)

    report = audit_project_store(store_dir)

    assert report.database_readable is True
    assert report.sqlite_integrity == "ok"
    assert report.foreign_key_violations == 0
    assert report.project_count == 1
    assert report.page_count == 1
    assert report.findings == ()
    assert report.to_dict()["healthy"] is True


def test_project_audit_warns_when_source_is_missing(tmp_path: Path) -> None:
    store_dir, source = _store_with_source(tmp_path)
    source.unlink()

    report = audit_project_store(store_dir)

    assert any(item.code == "source_missing" for item in report.findings)
    assert report.database_readable is True


def test_project_audit_warns_when_source_hash_changed(tmp_path: Path) -> None:
    store_dir, source = _store_with_source(tmp_path)
    source.write_bytes(b"replacement source bytes")

    report = audit_project_store(store_dir)

    finding = next(item for item in report.findings if item.code == "source_hash_mismatch")
    assert finding.severity == "warning"
    assert report.database_readable is True


def test_project_audit_errors_when_page_asset_is_missing(tmp_path: Path) -> None:
    store_dir, _source = _store_with_source(tmp_path)
    asset = store_dir / "assets" / "page-000001.png"
    asset.unlink()

    report = audit_project_store(store_dir)

    finding = next(item for item in report.findings if item.code == "page_asset_missing")
    assert finding.severity == "error"
    assert report.page_count == 1


def test_project_audit_errors_when_page_asset_hash_changed(tmp_path: Path) -> None:
    store_dir, _source = _store_with_source(tmp_path)
    asset = store_dir / "assets" / "page-000001.png"
    asset.write_bytes(b"tampered page image bytes")

    report = audit_project_store(store_dir)

    finding = next(
        item for item in report.findings if item.code == "page_asset_hash_mismatch"
    )
    assert finding.severity == "error"
    assert "SHA-256" in finding.message


def test_project_audit_reports_missing_database_as_fatal(tmp_path: Path) -> None:
    report = audit_project_store(tmp_path / "missing.scribeforge")

    assert report.database_readable is False
    assert report.findings[0].code == "database_missing"
    assert report.to_dict()["healthy"] is False


def test_project_audit_reports_unreadable_database_as_fatal(tmp_path: Path) -> None:
    store_dir = tmp_path / "broken.scribeforge"
    store_dir.mkdir()
    (store_dir / "project.sqlite3").write_bytes(b"not a SQLite database")

    report = audit_project_store(store_dir)

    assert report.database_readable is False
    assert report.findings[0].code == "database_unreadable"



def test_audit_cli_emits_stable_json_and_zero_exit_for_healthy_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir, _source = _store_with_source(tmp_path)

    code = run(["audit", "--store", str(store_dir), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["healthy"] is True
    assert payload["sqlite_integrity"] == "ok"
    assert payload["findings"] == []


def test_audit_cli_returns_partial_for_findings_and_fatal_for_missing_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir, source = _store_with_source(tmp_path)
    source.write_bytes(b"changed")

    partial = run(["audit", "--store", str(store_dir), "--json"])
    partial_output = capsys.readouterr()
    assert partial == 1
    assert "source_hash_mismatch" in partial_output.out

    fatal = run(["audit", "--store", str(tmp_path / "missing.scribeforge"), "--json"])
    fatal_output = capsys.readouterr()
    assert fatal == 2
    assert "database_missing" in fatal_output.out



def test_project_audit_detects_foreign_key_violations(tmp_path: Path) -> None:
    store_dir, _source = _store_with_source(tmp_path)
    database = store_dir / "project.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO pages(project_id, page_index, source_image, width, height)
            VALUES (9999, 7, '/missing/foreign-page.png', 10, 10)
            """
        )

    report = audit_project_store(store_dir)

    assert report.foreign_key_violations == 1
    assert any(item.code == "foreign_key_violations" for item in report.findings)


def test_project_audit_warns_when_store_has_no_project(tmp_path: Path) -> None:
    store_dir = tmp_path / "empty.scribeforge"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()

    report = audit_project_store(store_dir)

    finding = next(item for item in report.findings if item.code == "project_missing")
    assert finding.severity == "warning"
    assert report.project_count == 0


def test_project_audit_warns_when_store_contains_multiple_projects(tmp_path: Path) -> None:
    store_dir, source = _store_with_source(tmp_path)
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.create_project("Duplicate", str(source.resolve()), _sha256(source))

    report = audit_project_store(store_dir)

    finding = next(item for item in report.findings if item.code == "multiple_projects")
    assert finding.severity == "warning"
    assert report.project_count == 2


@pytest.mark.parametrize("column,value", [
    ("page_asset_sha256", None),
    ("page_asset_sha256", "invalid"),
    ("page_asset_byte_size", None),
    ("page_asset_parameters_json", None),
    ("page_asset_parameters_json", "broken"),
    ("page_asset_parameters_json", "[]"),
    ("page_asset_version", None),
])
def test_audit_reports_invalid_page_asset_evidence_without_crashing(
    tmp_path: Path, column: str, value: object
) -> None:
    root, _ = _store_with_source(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute(f"UPDATE pages SET {column}=?", (value,))
    report = audit_project_store(root)
    findings = [f for f in report.findings if f.code == "page_asset_evidence_invalid"]
    assert report.database_readable
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert not any(f.code == "page_asset_evidence_missing" for f in report.findings)


def test_invalid_page_evidence_blocks_backup_without_replacing_old_archive(tmp_path: Path) -> None:
    from scribeforge.project_backup import ProjectBackupError, create_project_backup

    root, _ = _store_with_source(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("UPDATE pages SET page_asset_byte_size=NULL")
    destination = tmp_path / "backup.zip"
    destination.write_bytes(b"existing backup")
    with pytest.raises(ProjectBackupError, match="page_asset_evidence_invalid"):
        create_project_backup(root, destination, overwrite=True)
    assert destination.read_bytes() == b"existing backup"


def test_audit_reports_recorded_size_mismatch(tmp_path: Path) -> None:
    root, _ = _store_with_source(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("UPDATE pages SET page_asset_byte_size=page_asset_byte_size+1")
    report = audit_project_store(root)
    assert any(f.code == "page_asset_size_mismatch" and f.severity == "error"
               for f in report.findings)


def test_audit_reports_partial_evidence_even_if_raster_is_missing(tmp_path: Path) -> None:
    root, _ = _store_with_source(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("UPDATE pages SET page_asset_byte_size=NULL")
    (root / "assets" / "page-000001.png").unlink()
    report = audit_project_store(root)
    codes = {finding.code for finding in report.findings}
    assert {"page_asset_evidence_invalid", "page_asset_missing"} <= codes
