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
