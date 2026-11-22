from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone

import pytest
from PIL import Image

from scribeforge.adapters.candidate_epub import build_candidate_epub
from scribeforge.book_diagnosis import diagnose_book
from scribeforge.cli import run
from scribeforge.domain.candidate import OcrCandidateBook, OcrCandidatePage
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.source import RenderedPage
from scribeforge.pipeline.assembly import assemble_verified_content
from scribeforge.pipeline.importing import import_source
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.storage.sqlite import SQLiteStore


class Source:
    page_count = 4

    def __init__(self, fail: bool = False):
        self.fail = fail

    def render_page(self, page_index: int) -> RenderedPage:
        if self.fail and page_index == 2:
            raise RuntimeError("injected interruption")
        buffer = io.BytesIO()
        Image.new("RGB", (30, 40), "white").save(buffer, format="PNG")
        return RenderedPage(page_index, 30, 40, buffer.getvalue(), "png")


class Engine:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def analyze_page(self, image_path, page_index):
        self.calls.append(page_index)
        return PageOCRResult(
            page_index, self.name, "fake", (OCRLine("body", BoundingBox(0, 0, 0.5, 0.1)),)
        )


def test_partial_import_cannot_export_then_resumes_preserving_page_evidence(tmp_path, capsys):
    db = tmp_path / "project.sqlite3"
    store = SQLiteStore(db)
    store.initialize()
    pid = store.create_project("book", "source.pdf", "hash")
    with pytest.raises(RuntimeError, match="injected interruption"):
        import_source(store, pid, Source(fail=True), tmp_path / "assets")
    primary, secondary = Engine("one"), Engine("two")
    process_book(
        store,
        pid,
        primary_engine=primary,
        secondary_engine=secondary,
        crops_dir=tmp_path / "crops",
        max_workers=1,
    )
    original_pages = store.list_pages(pid)
    original_runs = [store.latest_engine_run(pid, i, "one") for i in range(2)]
    with pytest.raises(ValueError, match="import is failed"):
        assemble_verified_content(store, pid)
    epub = tmp_path / "partial.epub"
    assert run(["export", "--store", str(tmp_path), "--out", str(epub)]) == 2
    assert not epub.exists()
    assert "import is failed" in capsys.readouterr().err
    assert (
        run(
            [
                "export-candidates",
                "--store",
                str(tmp_path),
                "--out-dir",
                str(tmp_path / "out"),
                "--engine",
                "one",
            ]
        )
        == 2
    )
    assert not (tmp_path / "out").exists()
    assert "import is failed" in capsys.readouterr().err
    resumed = import_source(store, pid, Source(), tmp_path / "assets")
    assert resumed.imported_page_indexes == (2, 3)
    assert store.list_pages(pid)[:2] == original_pages
    process_book(
        store,
        pid,
        primary_engine=primary,
        secondary_engine=secondary,
        crops_dir=tmp_path / "crops",
        max_workers=2,
    )
    assert sorted(primary.calls) == [0, 1, 2, 3]
    assert [store.latest_engine_run(pid, i, "one") for i in range(2)] == original_runs
    assert len(assemble_verified_content(store, pid).pages) == 4
    assert store.load_job(resumed.job_id).state is JobState.SUCCEEDED


def test_incomplete_import_running_pending_and_completed_hole(tmp_path):
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", "x.pdf", "hash")
    store.add_page(pid, 1, "page.png", 10, 10)
    job = store.ensure_job(pid, JobStage.IMPORT)
    for expected in ("pending", "running"):
        with pytest.raises(ValueError, match=f"import is {expected}"):
            assemble_verified_content(store, pid)
        if expected == "pending":
            store.start_job(job)
    store.complete_job(job)
    with pytest.raises(ValueError, match="missing physical page indexes"):
        assemble_verified_content(store, pid)


def test_diagnose_counts_pdf_project_and_candidate_markers(tmp_path, capsys):
    source = tmp_path / "book.pdf"
    pages = [Image.new("RGB", (20, 20), "white") for _ in range(4)]
    pages[0].save(source, save_all=True, append_images=pages[1:])
    db = tmp_path / "project.sqlite3"
    store = SQLiteStore(db)
    store.initialize()
    pid = store.create_project("book", str(source), hashlib.sha256(source.read_bytes()).hexdigest())
    job = store.ensure_job(pid, JobStage.IMPORT)
    store.start_job(job)
    store.add_page(pid, 0, "p0.png", 20, 20)
    store.add_page(pid, 1, "p1.png", 20, 20)
    store.fail_job(job, "interrupted")
    candidate = OcrCandidateBook(
        "book",
        "one",
        (OcrCandidatePage(0, 1, "fake", ("text",)), OcrCandidatePage(1, 2, "fake", ("text",))),
    )
    epub = tmp_path / "partial.epub"
    epub.write_bytes(
        build_candidate_epub(
            candidate,
            identifier="id",
            language="en",
            modified=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
    )
    before = db.read_bytes()
    result = diagnose_book(db, source, epub)
    assert result["source_physical_pages"] == 4
    assert result["imported_pages"] == 2
    assert result["missing_imported_pages_1based"] == [3, 4]
    assert result["import_state"] == "failed"
    assert result["epub"]["source_page_markers"] == 2
    assert result["epub"]["missing_source_page_markers_1based"] == [3, 4]
    assert db.read_bytes() == before
    assert (
        run(
            [
                "diagnose-book",
                "--store",
                str(tmp_path),
                "--source",
                str(source),
                "--epub",
                str(epub),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["source_physical_pages"] == 4
    source.write_bytes(b"different")
    assert (
        run(
            [
                "diagnose-book",
                "--store",
                str(tmp_path),
                "--source",
                str(source),
                "--epub",
                str(epub),
            ]
        )
        == 2
    )
    assert "SHA-256" in capsys.readouterr().err


def test_diagnosis_rejects_duplicate_archive_entries(tmp_path):
    from scribeforge.book_diagnosis import _epub_page_evidence

    epub = tmp_path / "bad.epub"
    with zipfile.ZipFile(epub, "w") as z:
        z.writestr("OEBPS/content.opf", "x")
        with pytest.warns(UserWarning):
            z.writestr("OEBPS/content.opf", "x")
    with pytest.raises(ValueError, match="duplicate"):
        _epub_page_evidence(epub)


def test_trusted_epub_pagebreaks_count_not_spine_documents(tmp_path):
    from scribeforge.adapters.epub import build_epub
    from scribeforge.book_diagnosis import _epub_page_evidence
    from scribeforge.domain.content import VerifiedBook, VerifiedChapter, VerifiedPage

    book = VerifiedBook(
        "test",
        tuple(VerifiedPage(index, (f"body {index}.",)) for index in range(4)),
        chapters=(VerifiedChapter("Chapter", 1, 0),),
    )
    path = tmp_path / "chapter.epub"
    path.write_bytes(
        build_epub(
            book,
            identifier="id",
            language="en",
            modified=datetime(2024, 1, 1, tzinfo=timezone.utc),
            layout="chapter",
        )
    )
    report = _epub_page_evidence(path)
    assert report["spine_documents"] == 1
    assert report["source_page_markers"] == 4
    assert report["page_indexes"] == [0, 1, 2, 3]
    assert report["evidence_styles"] == ["pagebreak_markers"]


def test_readiness_blocks_failed_import_and_explains_resume(tmp_path):
    from scribeforge.export_readiness import ReadinessOptions, read_export_readiness

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", "x.pdf", "hash")
    job = store.ensure_job(pid, JobStage.IMPORT)
    store.start_job(job)
    store.fail_job(job, "interrupted")
    report = read_export_readiness(tmp_path / "project.sqlite3", ReadinessOptions())
    assert report["ready"] is False
    assert report["issue_counts"]["evidence_error"] == 1
    assert "Resume processing" in report["issues"][0]["detail"]
