"""Read-only audit of persisted OCR frame and crop provenance."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.page_asset import PageAssetEvidence
from scribeforge.domain.preprocess import PreprocessSettings
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.orchestrator import process_page
from scribeforge.pipeline.rounds import RepairReader, run_repair_round
from scribeforge.project_audit import audit_project_store
from scribeforge.storage.sqlite import SQLiteStore


class Engine:
    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        return PageOCRResult(
            page_index, self.name, "1", (OCRLine(self.text, BoundingBox(0.1, 0.1, 0.4, 0.1)),)
        )


class Reviewer:
    def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
        return VLMReading(page_index, pair_index, "vlm", "1", "待复核", True)


def prepared(tmp_path: Path):
    root = tmp_path / "book.scribeforge"
    assets = root / "assets"
    assets.mkdir(parents=True)
    source = tmp_path / "book.pdf"
    source.write_bytes(b"book source")
    image = assets / "page.png"
    Image.new("RGB", (100, 100), "white").save(image)
    store = SQLiteStore(root / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", str(source), hashlib.sha256(source.read_bytes()).hexdigest())
    store.add_page(
        pid, 0, str(image), 100, 100, page_asset=PageAssetEvidence.from_bytes(image.read_bytes())
    )
    result = process_page(
        store,
        pid,
        store.list_pages(pid)[0],
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        preprocess=PreprocessSettings(),
        crops_dir=root / "crops",
    )
    run_repair_round(
        store, pid, readers=(RepairReader("c", Engine("c", "丙")),), crops_dir=root / "crops"
    )
    return root, store, pid, result


def test_audit_reports_tampered_processed_frame(tmp_path: Path) -> None:
    root, store, pid, result = prepared(tmp_path)
    assert audit_project_store(root).healthy
    record = store.load_preprocess(pid, 0)
    assert record is not None
    Image.new("RGB", (record.output_width, record.output_height), "black").save(record.output_image)
    report = audit_project_store(root)
    assert any(
        f.code == "ocr_frame_hash_mismatch" and f.severity == "error" for f in report.findings
    )
    assert store.load_ocr_frame(result.primary_run_id).sha256 == record.output_sha256


def test_audit_reports_tampered_crop_and_invalid_geometry(tmp_path: Path) -> None:
    root, store, _, result = prepared(tmp_path)
    review = store.list_vlm_reviews(result.verification_id)[0]
    Path(review.crop_path).write_bytes(b"tampered")
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("UPDATE crop_input_evidence SET x=1000 WHERE vlm_review_id=?", (review.id,))
    report = audit_project_store(root)
    assert any(f.code == "crop_geometry_invalid" and f.severity == "error" for f in report.findings)
    assert any(f.code == "crop_hash_mismatch" and f.severity == "error" for f in report.findings)


def test_audit_warns_unknown_legacy_provenance_without_guessing_or_mutating(tmp_path: Path) -> None:
    root, store, _, result = prepared(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("DELETE FROM crop_input_evidence")
        db.execute("DELETE FROM ocr_input_frames WHERE run_id=?", (result.primary_run_id,))
        before = list(db.iterdump())
    report = audit_project_store(root)
    with sqlite3.connect(root / "project.sqlite3") as db:
        assert list(db.iterdump()) == before
    codes = {f.code for f in report.findings if f.severity == "warning"}
    assert "ocr_frame_unknown" in codes
    assert "crop_provenance_unknown" in codes
    assert store.load_ocr_frame(result.primary_run_id) is None


def test_audit_reports_missing_crop_and_prevent_backup(tmp_path: Path) -> None:
    from scribeforge.project_backup import ProjectBackupError, create_project_backup

    root, store, _, result = prepared(tmp_path)
    Path(store.list_vlm_reviews(result.verification_id)[0].crop_path).unlink()
    assert any(f.code == "crop_missing" for f in audit_project_store(root).findings)
    with pytest.raises(ProjectBackupError, match="crop_missing"):
        create_project_backup(root, tmp_path / "backup.zip")


def test_audit_flags_verification_with_mixed_frames_even_without_crops(tmp_path: Path) -> None:
    root, store, _, result = prepared(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("DELETE FROM crop_input_evidence")
        db.execute(
            "UPDATE ocr_input_frames SET sha256=? WHERE run_id=?",
            ("0" * 64, result.secondary_run_id),
        )
    report = audit_project_store(root)
    assert any(
        f.code == "verification_frame_mismatch" and f.severity == "error" for f in report.findings
    )
    assert store.load_ocr_frame(result.primary_run_id) != store.load_ocr_frame(
        result.secondary_run_id
    )


def test_audit_v13_store_does_not_migrate_or_guess_crop_geometry(tmp_path: Path) -> None:
    root, store, _, result = prepared(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("DROP TABLE crop_input_evidence")
        db.execute("PRAGMA user_version = 13")
        before = list(db.iterdump())
    report = audit_project_store(root)
    assert any(
        f.code == "crop_provenance_unknown" and f.severity == "warning" for f in report.findings
    )
    with sqlite3.connect(root / "project.sqlite3") as db:
        assert list(db.iterdump()) == before
        assert db.execute("PRAGMA user_version").fetchone()[0] == 13
    assert store.list_vlm_reviews(result.verification_id)


def test_audit_cli_reports_crop_mismatch_in_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from scribeforge.cli import run

    root, store, _, result = prepared(tmp_path)
    Path(store.list_vlm_reviews(result.verification_id)[0].crop_path).write_bytes(b"changed")
    assert run(["audit", "--store", str(root), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["healthy"] is False
    assert any(f["code"] == "crop_hash_mismatch" for f in payload["findings"])


def test_audit_v12_store_reports_unknown_without_creating_tables(tmp_path: Path) -> None:
    root, _, _, _ = prepared(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("DROP TABLE crop_input_evidence")
        db.execute("DROP TABLE ocr_input_frames")
        db.execute("PRAGMA user_version = 12")
        before = list(db.iterdump())
    report = audit_project_store(root)
    assert report.database_readable
    assert {"ocr_frame_unknown", "crop_provenance_unknown"} <= {f.code for f in report.findings}
    with sqlite3.connect(root / "project.sqlite3") as db:
        assert list(db.iterdump()) == before
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12


def test_legacy_unknown_provenance_warns_but_does_not_block_backup(tmp_path: Path) -> None:
    from scribeforge.project_backup import create_project_backup

    root, _, _, result = prepared(tmp_path)
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("DELETE FROM crop_input_evidence")
        db.execute("DELETE FROM ocr_input_frames WHERE run_id=?", (result.primary_run_id,))
    archive = tmp_path / "legacy-backup.zip"
    create_project_backup(root, archive)
    assert archive.is_file()


def test_audit_still_checks_historical_vlm_crop_digest_when_geometry_unknown(
    tmp_path: Path,
) -> None:
    root, store, _, result = prepared(tmp_path)
    review = store.list_vlm_reviews(result.verification_id)[0]
    with sqlite3.connect(root / "project.sqlite3") as db:
        db.execute("DELETE FROM crop_input_evidence WHERE vlm_review_id=?", (review.id,))
    Path(review.crop_path).write_bytes(b"altered")
    report = audit_project_store(root)
    assert any(
        f.code == "crop_provenance_unknown" and f.severity == "warning" for f in report.findings
    )
    assert any(
        f.code == "legacy_vlm_crop_hash_mismatch" and f.severity == "error" for f in report.findings
    )


def test_audit_checks_preprocessed_asset_even_before_ocr(tmp_path: Path) -> None:
    from scribeforge.pipeline.preprocessing import preprocess_page

    root = tmp_path / "book.scribeforge"
    root.mkdir()
    source = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(source)
    store = SQLiteStore(root / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", str(source), hashlib.sha256(source.read_bytes()).hexdigest())
    store.add_page(
        pid, 0, str(source), 100, 100, page_asset=PageAssetEvidence.from_bytes(source.read_bytes())
    )
    prepared_page = preprocess_page(
        store, pid, store.list_pages(pid)[0], PreprocessSettings(), assets_dir=root / "preprocessed"
    )
    Image.new("RGB", (prepared_page.width, prepared_page.height), "black").save(
        prepared_page.image_path
    )
    assert any(
        f.code == "preprocess_hash_mismatch" and f.severity == "error"
        for f in audit_project_store(root).findings
    )


@pytest.mark.parametrize("kind", ["preprocessed", "crop", "legacy_crop", "region_crop"])
def test_backup_rejects_references_to_unarchived_external_evidence(
    tmp_path: Path, kind: str
) -> None:
    import shutil

    from scribeforge.project_backup import ProjectBackupError, create_project_backup

    root, store, pid, result = prepared(tmp_path)
    if kind == "preprocessed":
        original = Path(store.load_preprocess(pid, 0).output_image)
    elif kind == "region_crop":
        with sqlite3.connect(root / "project.sqlite3") as db:
            original = Path(db.execute(
                "SELECT crop_path FROM crop_input_evidence WHERE ocr_run_id IS NOT NULL"
            ).fetchone()[0])
    else:
        original = Path(store.list_vlm_reviews(result.verification_id)[0].crop_path)
    external = tmp_path / "external" / original.name
    external.parent.mkdir()
    shutil.move(original, external)
    with sqlite3.connect(root / "project.sqlite3") as db:
        if kind == "preprocessed":
            db.execute("UPDATE page_preprocess SET output_image=?", (str(external),))
        else:
            db.execute("UPDATE vlm_reviews SET crop_path=? WHERE crop_path=?",
                       (str(external), str(original)))
            if kind == "legacy_crop":
                db.execute("DELETE FROM crop_input_evidence WHERE crop_path=?", (str(original),))
            else:
                db.execute("UPDATE crop_input_evidence SET crop_path=? WHERE crop_path=?",
                           (str(external), str(original)))
    assert not [f for f in audit_project_store(root).findings if f.severity in {"error", "fatal"}]
    archive = tmp_path / "backup.zip"
    with pytest.raises(ProjectBackupError, match="missing or ambiguously maps"):
        create_project_backup(root, archive)
    assert not archive.exists()


@pytest.mark.parametrize("kind", ["frame", "crop"])
def test_backup_refuses_evidence_changed_after_live_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from scribeforge import project_backup

    root, store, pid, result = prepared(tmp_path)
    path = (Path(store.load_preprocess(pid, 0).output_image) if kind == "frame"
            else Path(store.list_vlm_reviews(result.verification_id)[0].crop_path))
    snapshot = project_backup._snapshot_database

    def snapshot_then_change(source: Path, destination: Path) -> None:
        snapshot(source, destination)
        Image.new("RGB", (100, 100), "black").save(path)

    monkeypatch.setattr(project_backup, "_snapshot_database", snapshot_then_change)
    archive = tmp_path / "backup.zip"
    archive.write_bytes(b"previous backup")
    with pytest.raises(project_backup.ProjectBackupError, match="integrity"):
        project_backup.create_project_backup(root, archive, overwrite=True)
    assert archive.read_bytes() == b"previous backup"
    assert not list(tmp_path.glob(".backup.zip.*.tmp"))


def test_backup_rejects_snapshot_with_different_audit_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge import project_backup

    root, _, _, result = prepared(tmp_path)
    snapshot = project_backup._snapshot_database

    def snapshot_with_unknown_frame(source: Path, destination: Path) -> None:
        from contextlib import closing

        snapshot(source, destination)
        with closing(sqlite3.connect(destination)) as db, db:
            db.execute("DELETE FROM ocr_input_frames WHERE run_id=?", (result.primary_run_id,))

    monkeypatch.setattr(project_backup, "_snapshot_database", snapshot_with_unknown_frame)
    archive = tmp_path / "backup.zip"
    with pytest.raises(project_backup.ProjectBackupError, match="warnings.*changed"):
        project_backup.create_project_backup(root, archive)
    assert not archive.exists()


@pytest.mark.parametrize("directory", ["preprocessed", "crops"])
def test_verify_backup_rejects_omitted_referenced_evidence(
    tmp_path: Path, directory: str
) -> None:
    import json
    import zipfile

    from scribeforge.project_backup import (
        ProjectBackupError,
        create_project_backup,
        restore_project_backup,
        verify_project_backup,
    )

    root, _, _, _ = prepared(tmp_path)
    good = tmp_path / "good.zip"
    incomplete = tmp_path / "incomplete.zip"
    create_project_backup(root, good)
    with zipfile.ZipFile(good) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        removed = next(item["path"] for item in manifest["files"]
                       if item["path"].startswith(directory + "/"))
        manifest["files"] = [item for item in manifest["files"] if item["path"] != removed]
        with zipfile.ZipFile(incomplete, "w") as target:
            for name in archive.namelist():
                if name not in {removed, "manifest.json"}:
                    target.writestr(name, archive.read(name))
            target.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(ProjectBackupError, match="missing or ambiguously maps"):
        verify_project_backup(incomplete)
    with pytest.raises(ProjectBackupError, match="missing or ambiguously maps"):
        restore_project_backup(incomplete, tmp_path / "restore")
    assert not (tmp_path / "restore").exists()


def test_valid_backup_restores_evidence_without_the_original_store_or_source(tmp_path: Path) -> None:
    import shutil

    from scribeforge.project_backup import create_project_backup, restore_project_backup

    root, _, _, processed = prepared(tmp_path)
    archive = tmp_path / "backup.zip"
    create_project_backup(root, archive)
    shutil.rmtree(root)
    (tmp_path / "book.pdf").unlink()
    target = tmp_path / "restored"
    result = restore_project_backup(archive, target)
    assert result["healthy"] is True
    assert not [f for f in audit_project_store(target).findings if f.severity in {"error", "fatal"}]
    store = SQLiteStore(target / "project.sqlite3")
    pid = store.list_projects()[0].id
    assert Path(store.load_preprocess(pid, 0).output_image).is_relative_to(target)
    reviews = store.list_vlm_reviews(processed.verification_id)
    assert reviews
    for review in reviews:
        assert Path(review.crop_path).is_relative_to(target)
