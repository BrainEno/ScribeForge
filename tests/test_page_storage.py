from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.alignment import LineAlignment, PageAlignment
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import PairRisk, RiskReason
from scribeforge.storage.sqlite import SQLiteStore


def _store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    return store, store.create_project("Book", "book.pdf", "source-hash")


def test_list_pages_returns_ordered_page_evidence(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    store.add_page(project_id, 1, "pages/1.png", 10, 20)
    store.add_page(project_id, 0, "pages/0.png", 30, 40)

    assert store.imported_page_indexes(project_id) == frozenset({0, 1})

    pages = store.list_pages(project_id)
    assert [
        (page.page_index, page.source_image, page.width, page.height) for page in pages
    ] == [
        (0, "pages/0.png", 30, 40),
        (1, "pages/1.png", 10, 20),
    ]


def test_page_reads_reject_unknown_project(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    with pytest.raises(KeyError, match="unknown project"):
        store.imported_page_indexes(999)
    with pytest.raises(KeyError, match="unknown project"):
        store.list_pages(999)


def _ocr(text: str, page_index: int, engine: str = "primary") -> PageOCRResult:
    box = BoundingBox(0.1, 0.2, 0.5, 0.05)
    line = OCRLine(text, box, (OCRToken(text, 0.9, box),))
    return PageOCRResult(page_index, engine, "1.0", (line,))


def test_latest_engine_run_resolves_per_engine_evidence(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    store.add_page(project_id, 0, "pages/0.png", 10, 20)

    first = store.record_ocr(project_id, _ocr("鸦", 0))
    store.record_ocr(project_id, _ocr("鸦鸦", 0))

    assert store.latest_engine_run(project_id, 0, "primary") > first

    with pytest.raises(KeyError, match="unknown OCR run"):
        store.latest_engine_run(project_id, 0, "secondary")


def test_latest_verification_resolves_most_recent_comparison(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    store.add_page(project_id, 0, "pages/0.png", 10, 20)
    primary_id = store.record_ocr(project_id, _ocr("鸦", 0))
    secondary_id = store.record_ocr(project_id, _ocr("雅", 0, "secondary"))

    alignment = PageAlignment(0, "primary", "secondary", (LineAlignment(0, 0, 0.5, 0.0, ()),))
    risks = (PairRisk(0, 0.45, (RiskReason.TEXT_DISAGREEMENT,)),)
    verification_id = store.record_verification(primary_id, secondary_id, alignment, risks, ())

    assert store.latest_verification_id(project_id, 0) == verification_id

    with pytest.raises(KeyError, match="unknown verification"):
        store.latest_verification_id(project_id, 1)


def test_list_projects_returns_created_projects_in_order(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    first = store.create_project("A", "a.pdf", "hash-a")
    second = store.create_project("B", "b.pdf", "hash-b")

    projects = store.list_projects()

    assert [
        (project.id, project.title, project.source_uri, project.source_sha256)
        for project in projects
    ] == [
        (first, "A", "a.pdf", "hash-a"),
        (second, "B", "b.pdf", "hash-b"),
    ]


def test_list_jobs_returns_every_state_in_creation_order(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    store.add_page(project_id, 0, "pages/0.png", 10, 20)
    import_id = store.ensure_job(project_id, JobStage.IMPORT)
    ocr_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    store.start_job(ocr_id)

    jobs = store.list_jobs(project_id)

    assert [(job.id, job.stage, job.state, job.attempts) for job in jobs] == [
        (import_id, JobStage.IMPORT, JobState.PENDING, 0),
        (ocr_id, JobStage.PRIMARY_OCR, JobState.RUNNING, 1),
    ]


def test_list_jobs_rejects_unknown_project(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    with pytest.raises(KeyError, match="unknown project"):
        store.list_jobs(999)


def test_latest_decision_returns_most_recent_or_none(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    store.add_page(project_id, 0, "pages/0.png", 10, 20)

    assert store.latest_decision(project_id, 0, 0) is None

    store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="鸦",
        actor="automatic",
        evidence_refs=("engine_run:1",),
    )
    second = store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="雅",
        actor="human",
        evidence_refs=("engine_run:2",),
    )

    latest = store.latest_decision(project_id, 0, 0)
    assert latest is not None
    assert (latest.id, latest.selected_text, latest.actor) == (second, "雅", "human")

    with pytest.raises(KeyError, match="unknown project/page"):
        store.latest_decision(project_id, 9, 0)


def test_scoped_runs_record_what_was_actually_read(tmp_path: Path) -> None:
    """A repair re-read is evidence of a *region*, not of the whole page."""

    store, project_id = _store(tmp_path)
    store.add_page(project_id, 0, "pages/0.png", 10, 20)
    page_run = store.record_ocr(project_id, _ocr("鸦", 0))
    first = store.record_ocr(project_id, _ocr("鸦", 0), scope="pair:0")
    second = store.record_ocr(project_id, _ocr("鸦", 0), scope="pair:0")

    assert store.list_scoped_run_ids(project_id, 0, "pair:0") == (first, second)
    assert store.list_scoped_run_ids(project_id, 0, "pair:1") == ()
    # The whole-page run stays the newest run for its engine: a region re-read
    # must never masquerade as a fresher page reading.
    assert store.latest_engine_run(project_id, 0, "primary") == page_run

    with pytest.raises(ValueError, match="scope"):
        store.record_ocr(project_id, _ocr("鸦", 0), scope="  ")
    with pytest.raises(ValueError, match="scope"):
        store.list_scoped_run_ids(project_id, 0, "")


def test_v7_database_migrates_to_v8_adding_run_scope(tmp_path: Path) -> None:
    """v8 adds the column that lets a run describe a region instead of a page."""

    import sqlite3

    path = tmp_path / "legacy-v7.sqlite3"
    store = SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        store._create_v1(connection)
        store._create_v2(connection)
        store._create_v3(connection)
        store._create_v4(connection)
        store._create_v5(connection)
        store._create_v6(connection)
        store._create_v7(connection)
        connection.execute(
            "INSERT INTO projects(title, source_uri, source_sha256) VALUES (?, ?, ?)",
            ("Legacy", "old.pdf", "oldhash"),
        )
        connection.execute("PRAGMA user_version = 7")

    store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(engine_runs)").fetchall()
        }
        assert "scope" in columns
        assert connection.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Legacy"


def test_v8_database_migrates_to_v9_adding_risk_and_confirmation_columns(
    tmp_path: Path,
) -> None:
    """v9 stores what the acceptance gate reads, and why a page has no text.

    Both columns exist because the gate cannot be recomputed later: a decision
    accepted under an older policy must keep the risk score it was judged on, and a
    textless page must keep who said so and why (issues #75, #74).
    """

    import sqlite3

    path = tmp_path / "legacy-v8.sqlite3"
    store = SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        store._create_v1(connection)
        store._create_v2(connection)
        store._create_v3(connection)
        store._create_v4(connection)
        store._create_v5(connection)
        store._create_v6(connection)
        store._create_v7(connection)
        store._create_v8(connection)
        connection.execute(
            "INSERT INTO projects(title, source_uri, source_sha256) VALUES (?, ?, ?)",
            ("Legacy", "old.pdf", "oldhash"),
        )
        connection.execute("PRAGMA user_version = 8")

    store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        decision_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(decisions)").fetchall()
        }
        assert {"risk_score", "risk_reasons_json"} <= decision_columns
        risk_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(risks)").fetchall()
        }
        assert {"min_confidence", "content_score"} <= risk_columns
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "page_confirmations" in tables
        assert connection.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Legacy"


def test_v9_database_migrates_to_v10_adding_ink_and_chapter_source(
    tmp_path: Path,
) -> None:
    """v10 stores the evidence behind a role and where a chapter title came from.

    Without ``ink_ratio`` a textless page can only be guessed at; without
    ``chapters.source`` an export cannot say whether a title came from the book's
    own contents page or from one page's typography (issues #87, #74).
    """

    import sqlite3

    path = tmp_path / "legacy-v9.sqlite3"
    store = SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        store._create_v1(connection)
        store._create_v2(connection)
        store._create_v3(connection)
        store._create_v4(connection)
        store._create_v5(connection)
        store._create_v6(connection)
        store._create_v7(connection)
        store._create_v8(connection)
        store._create_v9(connection)
        connection.execute(
            "INSERT INTO projects(title, source_uri, source_sha256) VALUES (?, ?, ?)",
            ("Legacy", "old.pdf", "oldhash"),
        )
        connection.execute("PRAGMA user_version = 9")

    store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        structure_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(page_structure)").fetchall()
        }
        assert "ink_ratio" in structure_columns
        chapter_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(chapters)").fetchall()
        }
        assert "source" in chapter_columns
        assert (
            connection.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0]
            == "Legacy"
        )
