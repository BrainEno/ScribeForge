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

    finding = next(item for item in report.findings if item.code == "page_asset_hash_mismatch")
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


@pytest.mark.parametrize(
    "column,value",
    [
        ("page_asset_sha256", None),
        ("page_asset_sha256", "invalid"),
        ("page_asset_byte_size", None),
        ("page_asset_parameters_json", None),
        ("page_asset_parameters_json", "broken"),
        ("page_asset_parameters_json", "[]"),
        ("page_asset_version", None),
    ],
)
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
    assert any(
        f.code == "page_asset_size_mismatch" and f.severity == "error" for f in report.findings
    )


def test_audit_reports_partial_evidence_even_if_raster_is_missing(tmp_path: Path) -> None:
    root, _ = _store_with_source(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("UPDATE pages SET page_asset_byte_size=NULL")
    (root / "assets" / "page-000001.png").unlink()
    report = audit_project_store(root)
    codes = {finding.code for finding in report.findings}
    assert {"page_asset_evidence_invalid", "page_asset_missing"} <= codes


def _multibatch_store(tmp_path: Path) -> tuple[Path, SQLiteStore, int, int, int]:
    from scribeforge.domain.alignment import LineAlignment, PageAlignment
    from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
    from scribeforge.domain.risk import PairRisk

    root, _ = _store_with_source(tmp_path)
    store = SQLiteStore(root / "project.sqlite3")
    project = store.list_projects()[0].id
    line = OCRLine("甲", BoundingBox(0.1, 0.1, 0.5, 0.05))
    primary = store.record_ocr(project, PageOCRResult(0, "primary", "1", (line,)))
    secondary = store.record_ocr(project, PageOCRResult(0, "secondary", "1", (line,)))
    alignment = PageAlignment(0, "primary", "secondary", (LineAlignment(0, 0, 1.0, 0.0, ()),))
    old = store.record_verification(primary, secondary, alignment, (PairRisk(0, 0.0, ()),), ())
    new = store.record_verification(primary, secondary, alignment, (PairRisk(0, 0.0, ()),), ())
    return root, store, project, old, new


@pytest.mark.parametrize("kind", ["old", "uncited", "conflicting", "orphan", "malformed"])
def test_audit_reports_ambiguous_multibatch_decisions_readonly(tmp_path: Path, kind: str) -> None:
    root, store, project, old, new = _multibatch_store(tmp_path)
    refs = {
        "old": (f"verification_run:{old}",),
        "uncited": (),
        "conflicting": (f"verification_run:{old}", f"verification_run:{new}"),
        "orphan": (f"verification_run:{new}",),
        "malformed": (),
    }[kind]
    pair = 9 if kind == "orphan" else 0
    decision = store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=pair,
        selected_text="甲",
        actor="human",
        evidence_refs=refs,
    )
    if kind == "malformed":
        with sqlite3.connect(root / "project.sqlite3") as db:
            db.execute("UPDATE decisions SET evidence_refs_json = ? WHERE id = ?", ("{", decision))
    with sqlite3.connect(root / "project.sqlite3") as db:
        before = list(db.iterdump())
    report = audit_project_store(root)
    found = [f for f in report.findings if f.code == "decision_verification_ambiguous"]
    assert len(found) == 1
    assert found[0].severity == "warning"
    assert f"project {project}" in found[0].message
    assert "page 1" in found[0].message and f"pair {pair}" in found[0].message
    assert f"decision {decision}" in found[0].message and f"verification {new}" in found[0].message
    assert report.database_readable
    assert not report.healthy
    with sqlite3.connect(root / "project.sqlite3") as db:
        assert list(db.iterdump()) == before


def test_audit_ignores_superseded_decisions_and_accepts_current_binding(tmp_path: Path) -> None:
    root, store, project, old, new = _multibatch_store(tmp_path)
    assert not any(
        f.code == "decision_verification_ambiguous" for f in audit_project_store(root).findings
    )
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="old",
        actor="human",
        evidence_refs=(f"verification_run:{old}",),
    )
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="new",
        actor="human",
        evidence_refs=(f"verification_run:{new}",),
    )
    report = audit_project_store(root)
    assert not any(f.code == "decision_verification_ambiguous" for f in report.findings)
    assert len(store.list_decisions(project, 0, 0)) == 2


def test_ambiguous_history_backup_remains_available_and_carries_audit_warning(
    tmp_path: Path,
) -> None:
    from scribeforge.project_backup import (
        create_project_backup,
        restore_project_backup,
        verify_project_backup,
    )

    root, store, project, old, _ = _multibatch_store(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="甲",
        actor="human",
        evidence_refs=(f"verification_run:{old}",),
    )
    archive = tmp_path / "history.zip"
    create_project_backup(root, archive)
    verification = verify_project_backup(archive)
    assert any(
        f["code"] == "decision_verification_ambiguous" for f in verification["audit_warnings"]
    )
    restored = tmp_path / "restored.scribeforge"
    restore_project_backup(archive, restored)
    report = audit_project_store(restored)
    assert any(f.code == "decision_verification_ambiguous" for f in report.findings)
    restored_store = SQLiteStore(restored / "project.sqlite3")
    assert restored_store.list_decisions(project, 0, 0)[0].evidence_refs == (
        f"verification_run:{old}",
    )
    with pytest.raises(ValueError, match="ambiguous verification batch"):
        restored_store.latest_verification_id(project, 0)


def test_audit_cli_reports_ambiguous_history_in_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, store, project, old, _ = _multibatch_store(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="甲",
        actor="human",
        evidence_refs=(f"verification_run:{old}",),
    )
    assert run(["audit", "--store", str(root), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["database_readable"] is True
    assert payload["healthy"] is False
    assert any(item["code"] == "decision_verification_ambiguous" for item in payload["findings"])


def test_audit_observes_one_database_snapshot_when_a_decision_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge import project_audit

    root, store, project, old, _ = _multibatch_store(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    original = project_audit._audit_decision_bindings

    def add_after_snapshot(connection, findings):
        store.append_decision(
            project_id=project,
            page_index=0,
            pair_index=0,
            selected_text="甲",
            actor="human",
            evidence_refs=(f"verification_run:{old}",),
        )
        return original(connection, findings)

    monkeypatch.setattr(project_audit, "_audit_decision_bindings", add_after_snapshot)
    first = audit_project_store(root)
    assert first.database_readable
    assert not any(f.code == "decision_verification_ambiguous" for f in first.findings)
    monkeypatch.setattr(project_audit, "_audit_decision_bindings", original)
    second = audit_project_store(root)
    assert any(f.code == "decision_verification_ambiguous" for f in second.findings)


def test_single_batch_uncited_decision_is_audited_as_unknown_and_preserved(tmp_path: Path) -> None:
    from scribeforge.domain.alignment import LineAlignment, PageAlignment
    from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
    from scribeforge.domain.risk import PairRisk
    from scribeforge.project_backup import create_project_backup, verify_project_backup

    root, _ = _store_with_source(tmp_path)
    store = SQLiteStore(root / "project.sqlite3")
    project = store.list_projects()[0].id
    line = OCRLine("甲", BoundingBox(0.1, 0.1, 0.5, 0.05))
    a = store.record_ocr(project, PageOCRResult(0, "a", "1", (line,)))
    b = store.record_ocr(project, PageOCRResult(0, "b", "1", (line,)))
    verification = store.record_verification(
        a,
        b,
        PageAlignment(0, "a", "b", (LineAlignment(0, 0, 1.0, 0.0, ()),)),
        (PairRisk(0, 0.0, ()),),
        (),
    )
    decision = store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="甲",
        actor="human",
        evidence_refs=(),
    )
    report = audit_project_store(root)
    finding = next(item for item in report.findings if item.code == "decision_verification_unknown")
    assert finding.severity == "warning" and f"decision {decision}" in finding.message
    assert store.latest_verification_id(project, 0) == verification
    archive = tmp_path / "legacy.zip"
    create_project_backup(root, archive)
    assert any(
        f["code"] == "decision_verification_unknown"
        for f in verify_project_backup(archive)["audit_warnings"]
    )
    assert store.list_decisions(project, 0, 0)[0].evidence_refs == ()


def test_audit_scopes_historical_multibatch_and_uncited_decisions(tmp_path: Path) -> None:
    # #150: a historical database (created before the forward write guard) with
    # a multi-batch page and a legacy uncited decision must be fully audited:
    # ambiguous bindings and unknown provenance are both reported, and the raw
    # evidence is preserved untouched.
    from scribeforge.domain.alignment import (
        AlignmentConflict,
        ConflictKind,
        LineAlignment,
        PageAlignment,
    )
    from scribeforge.domain.ocr import PageOCRResult
    from scribeforge.domain.risk import PairRisk, RiskReason

    store_dir = tmp_path / "Book.scribeforge"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    store.add_page(project_id, 1, "pages/1.png", 100, 200)

    def line(text: str, y: float) -> object:
        from scribeforge.domain.ocr import BoundingBox, OCRLine

        return OCRLine(text, BoundingBox(0.1, y, 0.5, 0.05))

    primary = store.record_ocr(
        project_id, PageOCRResult(0, "primary", "1.0", (line("鸦", 0.1), line("乙", 0.5)))
    )
    secondary = store.record_ocr(
        project_id, PageOCRResult(0, "secondary", "1.0", (line("雅", 0.1), line("乙", 0.5)))
    )
    alignment = PageAlignment(
        0,
        "primary",
        "secondary",
        (
            LineAlignment(
                0,
                0,
                0.5,
                0.0,
                (AlignmentConflict(ConflictKind.SUBSTITUTION, "鸦", "雅", 0, 1, 0, 1),),
            ),
            LineAlignment(1, 1, 1.0, 0.0, ()),
        ),
    )
    risks = (PairRisk(0, 0.5, (RiskReason.TEXT_DISAGREEMENT,)), PairRisk(1, 0.0, ()))
    old = store.record_verification(primary, secondary, alignment, risks, ())
    # Second batch with swapped pair semantics (what older versions allowed).
    store.record_verification(
        primary,
        secondary,
        PageAlignment(0, "primary", "secondary", tuple(reversed(alignment.pairs))),
        risks,
        (),
    )
    # Stale-cited decision on the multi-batch page -> ambiguous.
    store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=1,
        selected_text="乙",
        actor="human",
        evidence_refs=(f"verification_run:{old}",),
    )
    # Legacy decision with no verification citation on the single-batch page.
    primary1 = store.record_ocr(project_id, PageOCRResult(1, "primary", "1.0", (line("一", 0.1),)))
    secondary1 = store.record_ocr(
        project_id, PageOCRResult(1, "secondary", "1.0", (line("一", 0.1),))
    )
    single = store.record_verification(
        primary1,
        secondary1,
        PageAlignment(1, "primary", "secondary", (LineAlignment(0, 0, 1.0, 0.0, ()),)),
        (PairRisk(0, 0.0, ()),),
        (),
    )
    store.append_decision(
        project_id=project_id,
        page_index=1,
        pair_index=0,
        selected_text="一",
        actor="human",
        evidence_refs=(),
    )

    report = audit_project_store(store_dir)
    by_code: dict[str, list[str]] = {}
    for finding in report.findings:
        by_code.setdefault(finding.code, []).append(finding.message)
    assert any(
        "page 1" in m and "pair 1" in m for m in by_code.get("decision_verification_ambiguous", [])
    )
    assert any(
        "page 2" in m and "unknown" in m for m in by_code.get("decision_verification_unknown", [])
    )
    # Recovery path: raw decisions remain readable and are not rewritten.
    assert store.list_decisions(project_id, 0, 1)[0].selected_text == "乙"
    assert store.load_verification(old).alignment.pairs[1].primary_line_index == 1
    assert store.load_verification(single) is not None
