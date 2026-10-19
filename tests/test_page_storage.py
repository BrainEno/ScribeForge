from __future__ import annotations

from pathlib import Path

import pytest

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
