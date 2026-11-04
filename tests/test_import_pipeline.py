from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.page_asset import PageAssetEvidence
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


def test_import_source_records_page_asset_digest(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"

    result = import_source(store, project_id, FakePageSource(_pages(2)), assets)

    pages = store.list_pages(project_id)
    assert pages[0].page_asset_sha256 == PageAssetEvidence.from_bytes(
        b"bytes-0"
    ).sha256
    assert pages[0].page_asset_byte_size == len(b"bytes-0")
    assert pages[0].page_asset_version == 1
    assert pages[0].page_asset_parameters_json is not None
    assert pages[1].page_asset_sha256 == PageAssetEvidence.from_bytes(
        b"bytes-1"
    ).sha256

    job = store.load_job(result.job_id)
    assert job.state is JobState.SUCCEEDED


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


def test_shared_import_directory_never_overwrites_another_project_page(tmp_path: Path) -> None:
    store, first = _store(tmp_path)
    second = store.create_project("other", "other.pdf", "other-hash")
    assets = tmp_path / "assets"
    import_source(store, first, FakePageSource(_pages(1)), assets)
    saved = Path(store.list_pages(first)[0].source_image)
    before = saved.read_bytes()
    page = RenderedPage(0, 10, 20, b"other-page", "png")
    import_source(store, second, FakePageSource([page]), assets)
    second_path = Path(store.list_pages(second)[0].source_image)
    assert saved != second_path
    assert saved.read_bytes() == before
    assert second_path.read_bytes() == b"other-page"
    assert store.list_pages(first)[0].page_asset_sha256 == PageAssetEvidence.from_bytes(before).sha256


def test_import_preserves_existing_source_and_temporary_path(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"
    assets.mkdir()
    source = assets / "page-000000.png"
    temporary = assets / ".page-000000.png.tmp"
    source.write_bytes(b"existing evidence")
    temporary.write_bytes(b"existing temporary evidence")
    import_source(store, project_id, FakePageSource(_pages(1)), assets)
    output = Path(store.list_pages(project_id)[0].source_image)
    assert output != source
    assert output.read_bytes() == b"bytes-0"
    assert source.read_bytes() == b"existing evidence"
    assert temporary.read_bytes() == b"existing temporary evidence"


def test_failed_page_record_does_not_overwrite_orphan_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"

    def fail(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "add_page", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        import_source(store, project_id, FakePageSource(_pages(1)), assets)
    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    assert store.load_job(job_id).state is JobState.FAILED
    orphan = assets / "page-000000.png"
    original = orphan.read_bytes()
    assert store.list_pages(project_id) == ()
    monkeypatch.undo()
    new_page = RenderedPage(0, 10, 20, b"new-render", "png")
    import_source(store, project_id, FakePageSource([new_page]), assets)
    recorded = Path(store.list_pages(project_id)[0].source_image)
    assert recorded != orphan
    assert orphan.read_bytes() == original
    assert recorded.read_bytes() == b"new-render"
    assert store.load_job(job_id).state is JobState.SUCCEEDED


def test_unusable_asset_directory_fails_job_and_releases_writer(
    tmp_path: Path,
) -> None:
    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"
    assets.write_bytes(b"not a directory")
    with pytest.raises(OSError):
        import_source(store, project_id, FakePageSource(_pages(1)), assets)
    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    assert store.load_job(job_id).state is JobState.FAILED
    assets.unlink()
    result = import_source(store, project_id, FakePageSource(_pages(1)), assets)
    assert result.imported_page_indexes == (0,)
    assert store.load_job(job_id).state is JobState.SUCCEEDED


def test_failed_asset_publication_cleans_temporary_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.pipeline import importing

    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"

    def fail_link(*args, **kwargs):
        raise OSError("hard link unavailable")

    monkeypatch.setattr(importing.os, "link", fail_link)
    with pytest.raises(OSError, match="hard link unavailable"):
        import_source(store, project_id, FakePageSource(_pages(1)), assets)
    assert not list(assets.iterdir())
    assert store.list_pages(project_id) == ()
    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    assert store.load_job(job_id).state is JobState.FAILED
    monkeypatch.undo()
    import_source(store, project_id, FakePageSource(_pages(1)), assets)
    assert store.load_job(job_id).state is JobState.SUCCEEDED
    assert Path(store.list_pages(project_id)[0].source_image).read_bytes() == b"bytes-0"
    assert not list(assets.glob(".import-*"))


def test_import_collision_paths_survive_backup_restore_without_original_store(tmp_path: Path) -> None:
    import hashlib
    import shutil

    from scribeforge.project_backup import create_project_backup, restore_project_backup

    root = tmp_path / "book.scribeforge"
    root.mkdir()
    source = tmp_path / "book.pdf"
    source.write_bytes(b"PDF source")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = SQLiteStore(root / "project.sqlite3")
    store.initialize()
    a = store.create_project("first", str(source), digest)
    b = store.create_project("second", str(source), digest)
    assets = root / "assets"
    import_source(store, a, FakePageSource(_pages(1)), assets)
    import_source(store, b, FakePageSource([RenderedPage(0, 10, 20, b"other", "png")]), assets)
    first_path = Path(store.list_pages(a)[0].source_image)
    second_path = Path(store.list_pages(b)[0].source_image)
    assert first_path != second_path
    archive = tmp_path / "backup.zip"
    create_project_backup(root, archive)
    shutil.rmtree(root)
    source.unlink()
    target = tmp_path / "restore"
    assert restore_project_backup(archive, target)["healthy"] is True
    restored = SQLiteStore(target / "project.sqlite3")
    assert Path(restored.list_pages(a)[0].source_image).read_bytes() == b"bytes-0"
    assert Path(restored.list_pages(b)[0].source_image).read_bytes() == b"other"
    assert Path(restored.list_pages(b)[0].source_image).is_relative_to(target)


def test_import_index_lookup_failure_marks_job_failed_and_releases_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project_id = _store(tmp_path)
    assets = tmp_path / "assets"

    def fail_lookup(*args, **kwargs):
        raise RuntimeError("could not load page index")

    monkeypatch.setattr(store, "imported_page_indexes", fail_lookup)
    with pytest.raises(RuntimeError, match="could not load page index"):
        import_source(store, project_id, FakePageSource(_pages(1)), assets)
    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    assert store.load_job(job_id).state is JobState.FAILED
    assert store.load_job(job_id).last_error == "could not load page index"
    monkeypatch.undo()
    assert import_source(store, project_id, FakePageSource(_pages(1)), assets).imported_page_indexes == (0,)
    assert store.load_job(job_id).state is JobState.SUCCEEDED


def test_concurrent_imports_publish_distinct_page_files(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, first = _store(tmp_path)
    second = store.create_project("second", "other.pdf", "hash")
    barrier = Barrier(2)
    assets = tmp_path / "assets"

    class SynchronizedSource(FakePageSource):
        def render_page(self, page_index: int) -> RenderedPage:
            result = super().render_page(page_index)
            barrier.wait(timeout=20)
            return result

    def run(project_id: int, data: bytes) -> None:
        instance = SQLiteStore(tmp_path / "project.sqlite3")
        try:
            import_source(instance, project_id,
                          SynchronizedSource([RenderedPage(0, 10, 20, data, "png")]), assets)
        finally:
            instance.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, first, b"first")
        b = pool.submit(run, second, b"second")
        a.result(timeout=30)
        b.result(timeout=30)
    a_path = Path(store.list_pages(first)[0].source_image)
    b_path = Path(store.list_pages(second)[0].source_image)
    assert a_path != b_path
    assert a_path.read_bytes() == b"first"
    assert b_path.read_bytes() == b"second"
