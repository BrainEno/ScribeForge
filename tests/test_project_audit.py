from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scribeforge.cli import run
from scribeforge.storage.sqlite import SQLiteStore
from scribeforge.support.project_audit import audit_project_store


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
    page_image.write_bytes(b"sample page image bytes")
    store.add_page(project_id, 0, str(page_image.resolve()), 100, 200)
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
    tmp_path: Path, capsys: object
) -> None:
    store_dir, _source = _store_with_source(tmp_path)

    code = run(["audit", "--store", str(store_dir), "--json"])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["healthy"] is True
    assert payload["sqlite_integrity"] == "ok"
    assert payload["findings"] == []


def test_audit_cli_returns_partial_for_findings_and_fatal_for_missing_store(
    tmp_path: Path, capsys: object
) -> None:
    store_dir, source = _store_with_source(tmp_path)
    source.write_bytes(b"changed")

    partial = run(["audit", "--store", str(store_dir), "--json"])
    partial_output = capsys.readouterr()  # type: ignore[attr-defined]
    assert partial == 1
    assert "source_hash_mismatch" in partial_output.out

    fatal = run(["audit", "--store", str(tmp_path / "missing.scribeforge"), "--json"])
    fatal_output = capsys.readouterr()  # type: ignore[attr-defined]
    assert fatal == 2
    assert "database_missing" in fatal_output.out
