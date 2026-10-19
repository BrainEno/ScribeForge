from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.source import RenderedPage
from scribeforge.pipeline.importing import import_source
from scribeforge.storage.sqlite import SQLiteStore


class FakePageSource:
    def __init__(self, pages: list[RenderedPage]) -> None:
        self._pages = pages

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def render_page(self, page_index: int) -> RenderedPage:
        if not 0 <= page_index < len(self._pages):
            raise IndexError(page_index)
        return self._pages[page_index]


def _store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    return store, store.create_project("Book", "book.pdf", "source-hash")


def _pages(count: int) -> list[RenderedPage]:
    return [
        RenderedPage(index, 10, 20, f"bytes-{index}".encode(), "png")
        for index in range(count)
    ]


def test_import_source_persists_pages_and_completes_job(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"

    result = import_source(store, project_id, FakePageSource(_pages(2)), assets)

    assert result.project_id == project_id
    assert result.page_count == 2
    assert result.imported_page_indexes == (0, 1)

    pages = store.list_pages(project_id)
    assert [page.page_index for page in pages] == [0, 1]
    assert [Path(page.source_image).name for page in pages] == [
        "page-000000.png",
        "page-000001.png",
    ]
    assert (assets / "page-000000.png").read_bytes() == b"bytes-0"

    job = store.load_job(result.job_id)
    assert job.state is JobState.SUCCEEDED
    assert job.stage is JobStage.IMPORT
    assert job.attempts == 1


def test_import_source_skips_already_imported_pages(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"

    first = import_source(store, project_id, FakePageSource(_pages(3)), assets)
    second = import_source(store, project_id, FakePageSource(_pages(3)), assets)

    assert first.imported_page_indexes == (0, 1, 2)
    assert second.imported_page_indexes == ()
    assert store.load_job(first.job_id).state is JobState.SUCCEEDED


def test_failed_import_marks_job_failed_and_resumes(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"

    class FlakyPageSource(FakePageSource):
        def render_page(self, page_index: int) -> RenderedPage:
            if page_index == 1:
                raise RuntimeError("page decode failure")
            return super().render_page(page_index)

    with pytest.raises(RuntimeError, match="page decode failure"):
        import_source(store, project_id, FlakyPageSource(_pages(2)), assets)

    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    failed = store.load_job(job_id)
    assert failed.state is JobState.FAILED
    assert failed.last_error == "page decode failure"
    assert store.imported_page_indexes(project_id) == frozenset({0})

    resumed = import_source(store, project_id, FakePageSource(_pages(2)), assets)

    assert resumed.imported_page_indexes == (1,)
    assert store.load_job(job_id).state is JobState.SUCCEEDED


def test_import_source_rejects_mismatched_rendered_page(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    class WrongIndexSource(FakePageSource):
        def render_page(self, page_index: int) -> RenderedPage:
            return RenderedPage(99, 10, 20, b"x", "png")

    with pytest.raises(ValueError, match="identity"):
        import_source(store, project_id, WrongIndexSource(_pages(1)), tmp_path / "assets")


def test_import_source_rejects_empty_source(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    with pytest.raises(ValueError, match="at least one page"):
        import_source(store, project_id, FakePageSource([]), tmp_path / "assets")
