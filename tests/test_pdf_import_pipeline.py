from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.pdfium import RenderedPdfPage
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.pipeline.pdf_import import import_pdf_pages, source_sha256
from scribeforge.storage.sqlite import SQLiteStore


class FixtureRasterizer:
    def __init__(self, page_count: int, *, fail_once_on: int | None = None) -> None:
        self._page_count = page_count
        self._fail_once_on = fail_once_on
        self.calls: list[int] = []
        self._failed = False

    def page_count(self, source: Path) -> int:
        assert source.exists()
        return self._page_count

    def render_page(
        self,
        source: Path,
        page_index: int,
        destination: Path,
        *,
        dpi: int,
    ) -> RenderedPdfPage:
        assert source.exists()
        assert dpi == 144
        self.calls.append(page_index)
        if page_index == self._fail_once_on and not self._failed:
            self._failed = True
            raise RuntimeError("fixture render failure")
        destination.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (40 + page_index, 60 + page_index), (page_index, 0, 0))
        image.save(destination, format="PNG")
        image.close()
        return RenderedPdfPage(
            page_index=page_index,
            path=destination,
            width=40 + page_index,
            height=60 + page_index,
            sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
        )


def _prepared(tmp_path: Path) -> tuple[Path, SQLiteStore, int, Path]:
    source = tmp_path / "book.pdf"
    source.write_bytes(b"%PDF-1.7\nfixture source bytes\n%%EOF\n")
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", str(source), source_sha256(source))
    page_dir = tmp_path / "pages"
    return source, store, project_id, page_dir


def test_import_renders_pages_incrementally_persists_hashes_and_completes_job(
    tmp_path: Path,
) -> None:
    source, store, project_id, page_dir = _prepared(tmp_path)
    rasterizer = FixtureRasterizer(3)

    result = import_pdf_pages(
        source,
        project_id,
        store,
        page_dir,
        rasterizer=rasterizer,
        dpi=144,
    )

    assert result.total_pages == 3
    assert result.imported_pages == (0, 1, 2)
    assert result.skipped_pages == ()
    assert rasterizer.calls == [0, 1, 2]
    pages = store.list_pages(project_id)
    assert [page.page_index for page in pages] == [0, 1, 2]
    for page in pages:
        path = Path(page.source_image)
        assert path.name == f"page-{page.page_index + 1:06d}.png"
        assert path.exists()
        assert page.source_image_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()

    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    job = store.load_job(job_id)
    assert job.state is JobState.SUCCEEDED
    assert job.attempts == 1


def test_completed_import_is_idempotent_and_does_not_rerender_pages(tmp_path: Path) -> None:
    source, store, project_id, page_dir = _prepared(tmp_path)
    rasterizer = FixtureRasterizer(2)
    first = import_pdf_pages(source, project_id, store, page_dir, rasterizer=rasterizer)
    first_bytes = {path.name: path.read_bytes() for path in page_dir.iterdir()}

    second = import_pdf_pages(source, project_id, store, page_dir, rasterizer=rasterizer)

    assert first.imported_pages == (0, 1)
    assert second.imported_pages == ()
    assert second.skipped_pages == (0, 1)
    assert rasterizer.calls == [0, 1]
    assert {path.name: path.read_bytes() for path in page_dir.iterdir()} == first_bytes
    job = store.load_job(store.ensure_job(project_id, JobStage.IMPORT))
    assert job.attempts == 1


def test_failed_import_resumes_from_first_unpersisted_page(tmp_path: Path) -> None:
    source, store, project_id, page_dir = _prepared(tmp_path)
    rasterizer = FixtureRasterizer(3, fail_once_on=1)

    with pytest.raises(RuntimeError, match="fixture render failure"):
        import_pdf_pages(source, project_id, store, page_dir, rasterizer=rasterizer)

    assert rasterizer.calls == [0, 1]
    assert [page.page_index for page in store.list_pages(project_id)] == [0]
    failed_job = store.load_job(store.ensure_job(project_id, JobStage.IMPORT))
    assert failed_job.state is JobState.FAILED
    assert failed_job.attempts == 1
    assert "fixture render failure" in (failed_job.last_error or "")

    resumed = import_pdf_pages(source, project_id, store, page_dir, rasterizer=rasterizer)

    assert resumed.imported_pages == (1, 2)
    assert resumed.skipped_pages == (0,)
    assert rasterizer.calls == [0, 1, 1, 2]
    completed_job = store.load_job(store.ensure_job(project_id, JobStage.IMPORT))
    assert completed_job.state is JobState.SUCCEEDED
    assert completed_job.attempts == 2


def test_import_refuses_source_hash_mismatch_before_creating_page_evidence(tmp_path: Path) -> None:
    source, store, project_id, page_dir = _prepared(tmp_path)
    source.write_bytes(b"different source")
    rasterizer = FixtureRasterizer(1)

    with pytest.raises(ValueError, match="source SHA-256"):
        import_pdf_pages(source, project_id, store, page_dir, rasterizer=rasterizer)

    assert rasterizer.calls == []
    assert store.list_pages(project_id) == ()
    assert store.list_resumable_jobs(project_id) == ()


def test_resume_detects_missing_or_changed_persisted_page_evidence(tmp_path: Path) -> None:
    source, store, project_id, page_dir = _prepared(tmp_path)
    rasterizer = FixtureRasterizer(2, fail_once_on=1)

    with pytest.raises(RuntimeError, match="fixture render failure"):
        import_pdf_pages(source, project_id, store, page_dir, rasterizer=rasterizer)

    page = store.list_pages(project_id)[0]
    Path(page.source_image).write_bytes(b"tampered")

    with pytest.raises(RuntimeError, match="page evidence hash"):
        import_pdf_pages(source, project_id, store, page_dir, rasterizer=rasterizer)

    assert rasterizer.calls == [0, 1]


def test_import_rejects_pdf_page_count_smaller_than_persisted_page_index(tmp_path: Path) -> None:
    source, store, project_id, page_dir = _prepared(tmp_path)
    rasterizer = FixtureRasterizer(2, fail_once_on=1)
    with pytest.raises(RuntimeError):
        import_pdf_pages(source, project_id, store, page_dir, rasterizer=rasterizer)
    assert [page.page_index for page in store.list_pages(project_id)] == [0]

    shrinking = FixtureRasterizer(0)
    with pytest.raises(RuntimeError, match="page count"):
        import_pdf_pages(source, project_id, store, page_dir, rasterizer=shrinking)
