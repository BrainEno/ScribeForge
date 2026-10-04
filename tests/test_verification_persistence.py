import sqlite3
from pathlib import Path

from scribeforge.domain.alignment import align_pages
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import assess_page, review_candidates
from scribeforge.storage.sqlite import SQLiteStore


def _line(text: str, y: float, confidence: float = 0.95) -> OCRLine:
    box = BoundingBox(0.1, y, 0.8, 0.05)
    return OCRLine(text, box, (OCRToken(text, confidence, box),))


def _result(engine: str, text: str) -> PageOCRResult:
    return PageOCRResult(0, engine, "1", (_line(text, 0.1),))


def _prepared(tmp_path: Path) -> tuple[SQLiteStore, int, int, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "source.pdf", "hash")
    store.add_page(project_id, 0, "pages/0001.png", 1000, 2000)
    primary_id = store.record_ocr(project_id, _result("mineru", "乌鸦。"))
    secondary_id = store.record_ocr(project_id, _result("paddleocr", "鸟鸦。"))
    return store, project_id, primary_id, secondary_id


def test_latest_schema_contains_verification_tables(tmp_path: Path) -> None:
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

    assert version == 3
    assert {
        "verification_runs",
        "alignments",
        "alignment_conflicts",
        "risks",
        "review_candidates",
    }.issubset(tables)


def test_verification_round_trips_alignment_risk_and_crop(tmp_path: Path) -> None:
    store, _project_id, primary_id, secondary_id = _prepared(tmp_path)
    primary = _result("mineru", "乌鸦。")
    secondary = _result("paddleocr", "鸟鸦。")
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary, threshold=0.3)

    verification_id = store.record_verification(
        primary_run_id=primary_id,
        secondary_run_id=secondary_id,
        alignment=alignment,
        risks=risks,
        candidates=candidates,
    )
    restored = store.load_verification(verification_id)

    assert restored.primary_run_id == primary_id
    assert restored.secondary_run_id == secondary_id
    assert restored.alignment == alignment
    assert restored.risks == risks
    assert restored.candidates == candidates


def test_recomputing_verification_is_append_only(tmp_path: Path) -> None:
    store, _project_id, primary_id, secondary_id = _prepared(tmp_path)
    primary = _result("mineru", "乌鸦。")
    secondary = _result("paddleocr", "鸟鸦。")
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary, threshold=0.3)

    first = store.record_verification(primary_id, secondary_id, alignment, risks, candidates)
    second = store.record_verification(primary_id, secondary_id, alignment, risks, candidates)

    assert first != second
    assert store.load_verification(first).alignment == alignment
    assert store.load_verification(second).alignment == alignment


def test_verification_rejects_engine_runs_from_different_pages(tmp_path: Path) -> None:
    store, project_id, primary_id, _secondary_id = _prepared(tmp_path)
    store.add_page(project_id, 1, "pages/0002.png", 1000, 2000)
    other = PageOCRResult(1, "paddleocr", "1", (_line("第二页", 0.1),))
    other_id = store.record_ocr(project_id, other)
    alignment = align_pages(_result("mineru", "乌鸦。"), _result("paddleocr", "鸟鸦。"))

    try:
        store.record_verification(primary_id, other_id, alignment, (), ())
    except ValueError as exc:
        assert "same page" in str(exc)
    else:
        raise AssertionError("verification should reject evidence from different pages")


def test_v1_database_migrates_without_losing_existing_project(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO projects(title, source_uri, source_sha256)
            VALUES ('Legacy', 'old.pdf', 'oldhash');
            PRAGMA user_version = 1;
            """
        )

    SQLiteStore(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Legacy"
