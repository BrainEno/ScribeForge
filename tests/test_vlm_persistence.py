from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from scribeforge.domain.alignment import align_pages
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import assess_page, review_candidates
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.vlm_review import ReviewEvidence
from scribeforge.storage.sqlite import SQLiteStore
from scribeforge.storage.vlm_state import SQLiteVLMReviewState


def _line(text: str, y: float, confidence: float = 0.95) -> OCRLine:
    box = BoundingBox(0.1, y, 0.8, 0.05)
    return OCRLine(text, box, (OCRToken(text, confidence, box),))


def _result(engine: str, text: str) -> PageOCRResult:
    return PageOCRResult(0, engine, "1", (_line(text, 0.1),))


def _prepared_verification(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "source.pdf", "hash")
    store.add_page(project_id, 0, "pages/0001.png", 1000, 2000)

    primary = _result("mineru", "乌鸦。")
    secondary = _result("paddleocr", "鸟鸦。")
    primary_id = store.record_ocr(project_id, primary)
    secondary_id = store.record_ocr(project_id, secondary)
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary, threshold=0.3)
    assert [candidate.pair_index for candidate in candidates] == [0]

    verification_id = store.record_verification(
        primary_id,
        secondary_id,
        alignment,
        risks,
        candidates,
    )
    return store, verification_id


def _reading(text: str = "乌鸦。", *, page_index: int = 0, pair_index: int = 0) -> VLMReading:
    return VLMReading(
        page_index=page_index,
        pair_index=pair_index,
        model="fixture-vlm",
        model_version="1",
        text=text,
        uncertain=False,
    )


def test_schema_v3_contains_append_only_vlm_evidence_table(tmp_path: Path) -> None:
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
    assert "vlm_reviews" in tables


def test_vlm_reviews_round_trip_and_remain_append_only(tmp_path: Path) -> None:
    store, verification_id = _prepared_verification(tmp_path)

    first = store.record_vlm_review(
        verification_id,
        _reading("乌鸦。"),
        crop_path="review/page-0000-pair-0000.png",
        crop_sha256="aaa",
    )
    second = store.record_vlm_review(
        verification_id,
        _reading("鸟鸦。"),
        crop_path="review/page-0000-pair-0000-rerun.png",
        crop_sha256="bbb",
    )

    records = store.list_vlm_reviews(verification_id)

    assert first != second
    assert [record.id for record in records] == [first, second]
    assert [record.reading.text for record in records] == ["乌鸦。", "鸟鸦。"]
    assert records[0].pair_index == 0
    assert records[0].crop_path == "review/page-0000-pair-0000.png"
    assert records[0].crop_sha256 == "aaa"
    assert records[0].reading == _reading("乌鸦。")
    assert store.reviewed_pair_indexes(verification_id) == frozenset({0})


def test_vlm_review_requires_a_matching_persisted_candidate(tmp_path: Path) -> None:
    store, verification_id = _prepared_verification(tmp_path)

    with pytest.raises(ValueError, match="candidate"):
        store.record_vlm_review(
            verification_id,
            _reading(pair_index=9),
            crop_path="crop.png",
            crop_sha256="hash",
        )

    with pytest.raises(ValueError, match="page"):
        store.record_vlm_review(
            verification_id,
            _reading(page_index=1),
            crop_path="crop.png",
            crop_sha256="hash",
        )

    assert store.list_vlm_reviews(verification_id) == ()


def test_v2_database_migrates_to_v3_without_losing_project_data(tmp_path: Path) -> None:
    path = tmp_path / "legacy-v2.sqlite3"
    store = SQLiteStore(path)
    with sqlite3.connect(path) as connection:
        store._create_v1(connection)
        store._create_v2(connection)
        connection.execute(
            "INSERT INTO projects(title, source_uri, source_sha256) VALUES (?, ?, ?)",
            ("Legacy", "old.pdf", "oldhash"),
        )
        connection.execute("PRAGMA user_version = 2")

    store.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT title FROM projects WHERE id = 1").fetchone()[0] == "Legacy"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='vlm_reviews'"
        ).fetchone()[0] == 1


def test_sqlite_vlm_state_hashes_crop_and_exposes_resume_state(tmp_path: Path) -> None:
    store, verification_id = _prepared_verification(tmp_path)
    crop = tmp_path / "crop.png"
    crop.write_bytes(b"literal crop evidence")
    state = SQLiteVLMReviewState(store, verification_id)

    assert state.completed_pairs(0) == frozenset()

    state.append(ReviewEvidence(reading=_reading(), crop_path=str(crop)))

    assert state.completed_pairs(0) == frozenset({0})
    record = store.list_vlm_reviews(verification_id)[0]
    assert record.crop_path == str(crop)
    assert record.crop_sha256 == hashlib.sha256(b"literal crop evidence").hexdigest()

    with pytest.raises(ValueError, match="page"):
        state.completed_pairs(1)
