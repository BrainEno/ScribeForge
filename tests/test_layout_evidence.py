"""Layout observations are hints, never verified pictures or accepted text."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.cli import run
from scribeforge.domain.alignment import LineAlignment, PageAlignment
from scribeforge.domain.ocr import BlockType, BoundingBox, OCRBlock, OCRLine, PageOCRResult
from scribeforge.domain.risk import PairRisk
from scribeforge.export_readiness import ReadinessOptions, read_export_readiness
from scribeforge.pipeline.layout_evidence import collect_layout_observations
from scribeforge.storage.sqlite import OCRInputFrame, SQLiteStore


@pytest.fixture
def project(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Mixed", "source.pdf", "hash")
    for page in range(3):
        path = tmp_path / f"{page}.png"
        Image.new("RGB", (100, 100), "white").save(path)
        store.add_page(project_id, page, str(path), 100, 100)
    return store, project_id


def _verification(
    store: SQLiteStore,
    project: int,
    page: int,
    first: tuple[OCRBlock, ...],
    second: tuple[OCRBlock, ...] = (),
    *,
    decide: bool = True,
    frame: OCRInputFrame | None = None,
) -> int:
    line = OCRLine("Accepted text.", BoundingBox(0.1, 0.1, 0.8, 0.1))
    run1 = store.record_ocr(
        project, PageOCRResult(page, "primary", "1", (line,), first), frame=frame
    )
    run2 = store.record_ocr(project, PageOCRResult(page, "secondary", "1", (line,), second))
    verification = store.record_verification(
        run1,
        run2,
        PageAlignment(page, "primary", "secondary", (LineAlignment(0, 0, 1, 0, ()),)),
        (PairRisk(0, 0, ()),),
        (),
    )
    if decide:
        store.append_decision(
            project_id=project,
            page_index=page,
            pair_index=0,
            selected_text=line.text,
            actor="human",
            evidence_refs=(),
        )
    return verification


def _block(kind: BlockType, x: float = 0.1) -> OCRBlock:
    return OCRBlock(kind, BoundingBox(x, 0.4, 0.2, 0.3))


def test_both_engines_and_exact_frame_are_preserved_without_merging(project) -> None:
    store, pid = project
    frame = OCRInputFrame("preprocessed", "a" * 64, 100, 100)
    verification = _verification(
        store,
        pid,
        0,
        (_block(BlockType.FIGURE), _block(BlockType.TABLE, 0.5)),
        (_block(BlockType.FIGURE),),
        frame=frame,
    )
    # An unverified newer run must not replace the run bound to the verification.
    store.record_ocr(pid, PageOCRResult(0, "primary", "later", (), (_block(BlockType.TABLE),)))
    observations = collect_layout_observations(store, pid)
    assert len(observations) == 1
    page = observations[0]
    assert page.page_index == 0 and page.may_be_omitted
    assert [region.block_type for region in page.regions] == [
        BlockType.FIGURE,
        BlockType.TABLE,
        BlockType.FIGURE,
    ]
    assert [region.engine for region in page.regions] == ["primary", "primary", "secondary"]
    assert [region.verification_id for region in page.regions] == [verification] * 3
    assert page.regions[0].as_json()["frame"] == {
        "kind": "preprocessed",
        "sha256": "a" * 64,
        "width": 100,
        "height": 100,
    }
    assert page.regions[2].as_json()["frame"] is None
    assert "1 observation(s) have unknown OCR frame" in page.warning()


def test_latest_verification_not_old_or_crop_evidence(project) -> None:
    store, pid = project
    _verification(store, pid, 0, (_block(BlockType.FIGURE),), decide=False)
    _verification(store, pid, 0, (_block(BlockType.TEXT),), decide=False)
    store.record_ocr(
        pid, PageOCRResult(0, "primary", "crop", (), (_block(BlockType.FIGURE),)), scope="crop:1"
    )
    assert collect_layout_observations(store, pid) == ()


def test_unverified_and_no_detection_are_not_claimed_clean(project) -> None:
    store, pid = project
    store.record_ocr(pid, PageOCRResult(1, "primary", "1", (), (_block(BlockType.FIGURE),)))
    _verification(store, pid, 0, ())
    assert collect_layout_observations(store, pid) == ()


def test_whole_page_image_distinction_does_not_erase_observations(project) -> None:
    store, pid = project
    _verification(store, pid, 0, (_block(BlockType.FIGURE),))
    (page,) = collect_layout_observations(store, pid, embedded_pages=frozenset({0}))
    assert page.embedded_page_image and not page.may_be_omitted
    assert page.as_json()["regions"]


def test_readiness_alert_is_nonblocking_bounded_scoped_and_read_only(project, tmp_path) -> None:
    store, pid = project
    _verification(store, pid, 0, (_block(BlockType.FIGURE), _block(BlockType.TABLE)))
    _verification(store, pid, 1, (_block(BlockType.TABLE),), decide=False)
    # Third page remains unverified. These blockers must not hide layout warnings.
    before = (tmp_path / "project.sqlite3").read_bytes()
    report = read_export_readiness(tmp_path / "project.sqlite3", ReadinessOptions(limit=1))
    assert report["blocking_count"] == 2
    assert report["issue_counts"] == {
        "unresolved_pair": 1,
        "unverified_page": 1,
        "layout_region": 2,
    }
    assert report["matching_count"] == 4 and len(report["issues"]) == 1
    scoped = read_export_readiness(tmp_path / "project.sqlite3", ReadinessOptions(page=0))
    assert scoped["blocking_count"] == 2 and scoped["matching_count"] == 1
    issue = scoped["issues"][0]
    assert issue["kind"] == "layout_region" and not issue["blocking"]
    assert "primary/figure: 1" in issue["detail"]
    assert (tmp_path / "project.sqlite3").read_bytes() == before


def test_export_emits_advisory_but_never_invents_images(project, tmp_path, capsys) -> None:
    store, pid = project
    for page in range(3):
        _verification(store, pid, page, (_block(BlockType.FIGURE),) if page == 0 else ())
    out = tmp_path / "result.epub"
    assert run(["export", "--store", str(tmp_path), "--out", str(out), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["images"] == 0
    assert payload["layout_observations"][0]["may_be_omitted"]
    assert payload["layout_observations"][0]["regions"][0]["type"] == "figure"
    assert "unreported regions may still exist" in payload["layout_notice"]
    with zipfile.ZipFile(out) as archive:
        contents = b"".join(
            archive.read(name) for name in archive.namelist() if name.endswith(".xhtml")
        )
    assert b"Accepted text." in contents
    assert b"<figure" not in contents
    assert run(["export", "--store", str(tmp_path), "--out", str(tmp_path / "second.epub")]) == 0
    text = capsys.readouterr().out
    assert "warning: page 0:" in text and "may miss figures" in text


def test_no_detected_regions_still_discloses_detection_limit(project, tmp_path, capsys) -> None:
    store, pid = project
    for page in range(3):
        _verification(store, pid, page, ())
    assert (
        run(["export", "--store", str(tmp_path), "--out", str(tmp_path / "x.epub"), "--json"]) == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["layout_observations"] == []
    assert "unreported regions may still exist" in payload["layout_notice"]


def _restore_v16_block_table(database: Path) -> None:
    """Reconstruct the old constraint while keeping the rest of the store intact."""
    import sqlite3

    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE ocr_blocks RENAME TO newer_blocks")
        db.execute("DROP INDEX idx_ocr_blocks_run")
        db.execute("""
            CREATE TABLE ocr_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES engine_runs(id) ON DELETE RESTRICT,
                block_index INTEGER NOT NULL CHECK(block_index >= 0),
                block_type TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
                width REAL NOT NULL, height REAL NOT NULL,
                line_start INTEGER NOT NULL CHECK(line_start >= 0),
                line_end INTEGER NOT NULL CHECK(line_end > line_start),
                UNIQUE(run_id, block_index)
            )
        """)
        db.execute("INSERT INTO ocr_blocks SELECT * FROM newer_blocks")
        db.execute("DROP TABLE newer_blocks")
        db.execute("CREATE INDEX idx_ocr_blocks_run ON ocr_blocks(run_id, block_index)")
        db.execute("PRAGMA user_version = 16")


def test_v16_migration_preserves_existing_blocks_and_monotonic_ids(project, tmp_path) -> None:
    import sqlite3

    store, pid = project
    existing = OCRBlock(BlockType.TEXT, BoundingBox(0.1, 0.1, 0.8, 0.1), 0, 1)
    verification_id = _verification(store, pid, 0, (existing,), decide=False)
    old_run = store.load_verification(verification_id).primary_run_id
    database = tmp_path / "project.sqlite3"
    _restore_v16_block_table(database)
    with sqlite3.connect(database) as db:
        old = db.execute("SELECT * FROM ocr_blocks").fetchall()
        db.execute("UPDATE sqlite_sequence SET seq = 100 WHERE name = 'ocr_blocks'")
    store.initialize()
    store.initialize()  # Reopening is idempotent.
    assert store.load_ocr_run(old_run).blocks == (existing,)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 19
        assert db.execute("SELECT * FROM ocr_blocks").fetchall() == old
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    new_run = store.record_ocr(pid, PageOCRResult(1, "surya", "1", (), (_block(BlockType.FIGURE),)))
    assert store.load_ocr_run(new_run).blocks == (_block(BlockType.FIGURE),)
    with sqlite3.connect(database) as db:
        assert (
            db.execute("SELECT id FROM ocr_blocks WHERE run_id = ?", (new_run,)).fetchone()[0] > 100
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE ocr_blocks SET line_end = -1 WHERE run_id = ?", (new_run,))


def test_failed_v17_migration_rolls_back_every_step(project, tmp_path) -> None:
    import sqlite3

    store, pid = project
    old_block = OCRBlock(BlockType.TEXT, BoundingBox(0.1, 0.1, 0.8, 0.1), 0, 1)
    _verification(store, pid, 0, (old_block,), decide=False)
    database = tmp_path / "project.sqlite3"
    _restore_v16_block_table(database)
    with sqlite3.connect(database) as db:
        rows = db.execute("SELECT * FROM ocr_blocks").fetchall()

        def deny_drop(action, name, arg2, database_name, source):
            if action == sqlite3.SQLITE_DROP_TABLE and name == "ocr_blocks":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(deny_drop)
        with pytest.raises(sqlite3.DatabaseError):
            SQLiteStore._migrate_v17(db)
        db.set_authorizer(None)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 16
        assert db.execute("SELECT * FROM ocr_blocks").fetchall() == rows
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name = 'ocr_blocks_v17'").fetchone()
            is None
        )
    store.initialize()
    assert store.load_ocr_run(
        store.load_verification(store.latest_verification_id(pid, 0)).primary_run_id
    ).blocks == (old_block,)


def test_block_browser_is_paginated_scoped_and_read_only(project, tmp_path) -> None:
    from scribeforge.storage.ocr_evidence import EvidenceQuery, read_ocr_evidence

    store, pid = project
    verification = _verification(
        store,
        pid,
        0,
        (_block(BlockType.FIGURE), _block(BlockType.TABLE), _block(BlockType.FIGURE, 0.5)),
    )
    run_id = store.load_verification(verification).primary_run_id
    database = tmp_path / "project.sqlite3"
    before = database.read_bytes()
    first = read_ocr_evidence(database, EvidenceQuery(pid, "blocks", page=0, run=run_id, limit=2))
    assert [item["block_index"] for item in first["rows"]] == [0, 1]
    assert first["next_after"] == 1
    assert first["rows"][0]["line_start"] == first["rows"][0]["line_end"] == 0
    second = read_ocr_evidence(
        database, EvidenceQuery(pid, "blocks", page=0, run=run_id, after=1, limit=2)
    )
    assert [item["block_index"] for item in second["rows"]] == [2]
    assert second["next_after"] is None
    with pytest.raises(ValueError, match="does not belong"):
        read_ocr_evidence(database, EvidenceQuery(pid, "blocks", page=1, run=run_id))
    with pytest.raises(ValueError, match="run is required"):
        EvidenceQuery(pid, "blocks", page=0)
    assert database.read_bytes() == before


def test_page_pipeline_persists_textless_regions_with_actual_input_frame(project, tmp_path) -> None:
    from scribeforge.pipeline.orchestrator import process_page

    store, pid = project

    class FixtureEngine:
        def __init__(self, name: str) -> None:
            self.name = name

        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            assert Path(image_path).is_file()
            line = OCRLine("Body.", BoundingBox(0.1, 0.1, 0.5, 0.1))
            return PageOCRResult(
                page_index, self.name, "fixture", (line,), (_block(BlockType.FIGURE),)
            )

    page = store.list_pages(pid)[0]
    outcome = process_page(
        store,
        pid,
        page,
        primary_engine=FixtureEngine("primary"),
        secondary_engine=FixtureEngine("secondary"),
        crops_dir=tmp_path / "crops",
    )
    result = store.load_ocr_run(outcome.primary_run_id)
    assert result.lines[0].text == "Body."
    assert result.blocks == (_block(BlockType.FIGURE),)
    (observation,) = collect_layout_observations(store, pid)
    assert len(observation.regions) == 2
    assert all(
        region.frame is not None and region.frame.kind == "original"
        for region in observation.regions
    )


def test_docx_reports_whole_page_image_as_omitted(project, tmp_path, capsys) -> None:
    store, pid = project
    for page in range(3):
        if page:
            _verification(store, pid, page, ())
            continue
        block = _block(BlockType.FIGURE)
        first = store.record_ocr(pid, PageOCRResult(page, "primary", "1", (), (block,)))
        second = store.record_ocr(pid, PageOCRResult(page, "secondary", "1", (), ()))
        store.record_verification(
            first, second, PageAlignment(page, "primary", "secondary", ()), (), ()
        )
        store.record_page_confirmation(
            project_id=pid, page_index=page, kind="non_text", actor="human", reason="illustration"
        )
    epub = tmp_path / "page.epub"
    docx = tmp_path / "page.docx"
    assert run(["export", "--store", str(tmp_path), "--out", str(epub), "--json"]) == 0
    epub_report = json.loads(capsys.readouterr().out)
    assert epub_report["images"] == 1
    assert epub_report["layout_observations"][0]["embedded_page_image"] is True
    assert run(["export", "--store", str(tmp_path), "--out", str(docx), "--json"]) == 0
    docx_report = json.loads(capsys.readouterr().out)
    assert docx_report["images"] == 0
    assert docx_report["cover_image"] is None
    assert docx_report["docx_omitted_page_images"] == [0]
    assert docx_report["layout_observations"][0]["embedded_page_image"] is False
    assert docx_report["layout_observations"][0]["may_be_omitted"] is True
