from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.alignment import LineAlignment, PageAlignment
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
