import sqlite3
from pathlib import Path

import pytest

from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.storage.sqlite import SQLiteStore


def _result(text: str, confidence: float | None = 0.93) -> PageOCRResult:
    box = BoundingBox(0.1, 0.2, 0.5, 0.05)
    token = OCRToken(text=text, confidence=confidence, box=box)
    line = OCRLine(text=text, box=box, tokens=(token,))
    return PageOCRResult(
        page_index=0,
        engine="fixture",
        engine_version="1.2.3",
        lines=(line,),
    )


def _prepared_store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "source.pdf", "abc123")
    store.add_page(project_id, 0, "pages/0001.png", 1000, 2000)
    return store, project_id


def test_initialize_creates_versioned_core_schema(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "project.sqlite3"
    store = SQLiteStore(path)

    store.initialize()

    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert version == 1
    assert {
        "projects",
        "pages",
        "engine_runs",
        "ocr_lines",
        "ocr_tokens",
        "decisions",
    }.issubset(tables)


def test_ocr_evidence_round_trips_with_geometry_and_confidence(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)

    run_id = store.record_ocr(project_id, _result("乌鸦"))
    restored = store.load_ocr_run(run_id)

    assert restored == _result("乌鸦")


def test_none_token_confidence_round_trips(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)

    run_id = store.record_ocr(project_id, _result("模糊", confidence=None))

    assert store.load_ocr_run(run_id) == _result("模糊", confidence=None)


def test_empty_ocr_result_round_trips(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)
    result = PageOCRResult(0, "fixture", "1.2.3", ())

    run_id = store.record_ocr(project_id, result)

    assert store.load_ocr_run(run_id) == result


def test_new_engine_run_never_overwrites_previous_ocr_evidence(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)

    first_run = store.record_ocr(project_id, _result("乌鸦"))
    second_run = store.record_ocr(project_id, _result("鸟鸦"))

    assert first_run != second_run
    assert store.load_ocr_run(first_run).lines[0].text == "乌鸦"
    assert store.load_ocr_run(second_run).lines[0].text == "鸟鸦"


def test_decisions_are_append_only_and_keep_evidence_references(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)
    run_id = store.record_ocr(project_id, _result("乌鸦"))

    first_id = store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="乌鸦",
        actor="human",
        evidence_refs=(f"run:{run_id}:line:0",),
    )
    second_id = store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="鸟鸦",
        actor="human",
        evidence_refs=(f"run:{run_id}:line:0", "note:manual-correction"),
    )

    history = store.list_decisions(project_id, 0, 0)

    assert first_id != second_id
    assert [decision.selected_text for decision in history] == ["乌鸦", "鸟鸦"]
    assert history[0].evidence_refs == (f"run:{run_id}:line:0",)
    assert store.load_ocr_run(run_id).lines[0].text == "乌鸦"


def test_list_decisions_is_empty_before_any_decision(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)

    assert store.list_decisions(project_id, 0, 0) == ()


def test_page_index_is_unique_within_project(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        store.add_page(project_id, 0, "other.png", 1000, 2000)


def test_invalid_decision_actor_is_rejected(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)

    with pytest.raises(sqlite3.IntegrityError):
        store.append_decision(
            project_id=project_id,
            page_index=0,
            pair_index=0,
            selected_text="x",
            actor="unknown",
            evidence_refs=(),
        )


def test_unknown_page_and_run_raise_key_error(tmp_path: Path) -> None:
    store, project_id = _prepared_store(tmp_path)

    with pytest.raises(KeyError, match="unknown project/page"):
        store.record_ocr(project_id, PageOCRResult(1, "fixture", "1", ()))
    with pytest.raises(KeyError, match="unknown OCR run"):
        store.load_ocr_run(999)
