from __future__ import annotations

import json
import platform
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.cli import (
    Engines,
    default_benchmark_engine_factory,
    default_engine_factory,
    main,
    run,
)
from scribeforge.domain.jobs import JobStage
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.vlm import VLMReading
from scribeforge.runtime import probe as runtime_probe
from scribeforge.runtime.models import RuntimeLayout
from scribeforge.storage.sqlite import SQLiteStore


class FakeEngine:
    """Deterministic OCR engine standing in for a managed worker."""

    def __init__(self, name: str, text: str) -> None:
        self._name = name
        self._text = text

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        token = OCRToken(self._text, 0.9, box)
        line = OCRLine(self._text, box, (token,))
        return PageOCRResult(page_index, self._name, "1.0", (line,))


class ExplodingEngine:
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        raise RuntimeError("engine crashed")


class FakeReviewer:
    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading:
        return VLMReading(page_index, pair_index, "fake-vlm", "1.0", "verified", False)


class UncertainReviewer:
    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading:
        return VLMReading(page_index, pair_index, "fake-vlm", "1.0", "maybe", True)


def _book(tmp_path: Path) -> Path:
    book = tmp_path / "book.png"
    Image.new("RGB", (80, 120), "white").save(book)
    return book


_DEFAULT_REVIEWER = object()


def _factory(
    *, secondary: bool = True, reviewer: object = _DEFAULT_REVIEWER, text: str | None = None
):
    chosen_reviewer = FakeReviewer() if reviewer is _DEFAULT_REVIEWER else reviewer

    def factory(runtime_root: Path) -> Engines:
        secondary_engine = (
            FakeEngine("secondary", text if text is not None else "雅")
            if secondary
            else ExplodingEngine("secondary")
        )
        return Engines(
            primary=FakeEngine("primary", text if text is not None else "鸦"),
            secondary=secondary_engine,
            reviewer=chosen_reviewer,
        )

    return factory


def test_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        run([])


def test_process_command_imports_and_orchestrates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"

    code = run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())

    captured = capsys.readouterr()
    assert code == 0
    assert "imported 1 new page" in captured.out
    assert "0 failed" in captured.out
    assert "recorded 1 decision(s)" in captured.out
    assert "0 pair(s) need review" in captured.out

    store = SQLiteStore(store_dir / "project.sqlite3")
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].title == "book"
    assert [page.page_index for page in store.list_pages(projects[0].id)] == [0]
    stages = {job.stage for job in store.list_jobs(projects[0].id)}
    assert JobStage.PRIMARY_OCR in stages
    assert JobStage.VLM_REVIEW in stages


def test_process_command_defaults_store_next_to_book(tmp_path: Path) -> None:
    book = _book(tmp_path)

    code = run(["process", str(book)], engine_factory=_factory())

    assert code == 0
    assert (tmp_path / "book.scribeforge" / "project.sqlite3").exists()


def test_process_command_reports_partial_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    book = _book(tmp_path)

    code = run(
        ["process", str(book), "--store", str(tmp_path / "store")],
        engine_factory=_factory(secondary=False),
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "1 failed" in captured.out
    assert "page 0: engine crashed" in captured.out


def test_process_command_reports_pairs_needing_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    book = _book(tmp_path)

    code = run(
        ["process", str(book), "--store", str(tmp_path / "store")],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "recorded 0 decision(s)" in captured.out
    assert "1 pair(s) need review" in captured.out


def test_process_command_rejects_missing_book(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(
        ["process", str(tmp_path / "nope.pdf"), "--store", str(tmp_path / "store")],
        engine_factory=_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "not found" in captured.err.lower()


def test_process_command_rejects_unsupported_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    book = tmp_path / "scan.tiff"
    book.write_bytes(b"not really an image")

    code = run(
        ["process", str(book), "--store", str(tmp_path / "store")],
        engine_factory=_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "unsupported" in captured.err.lower()


def test_process_command_is_resumable_across_runs(tmp_path: Path) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"

    assert run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory()) == 0
    assert run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory()) == 0

    store = SQLiteStore(store_dir / "project.sqlite3")
    projects = store.list_projects()
    assert len(projects) == 1
    assert [page.page_index for page in store.list_pages(projects[0].id)] == [0]

def test_process_rejects_a_different_pdf_for_an_existing_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _book(tmp_path)
    second = tmp_path / "second.png"
    Image.new("RGB", (80, 120), "white").save(second)
    store_dir = tmp_path / "shared-store"

    assert run(["process", str(first), "--store", str(store_dir)], engine_factory=_factory()) == 0
    capsys.readouterr()

    code = run(["process", str(second), "--store", str(store_dir)], engine_factory=_factory())

    captured = capsys.readouterr()
    assert code == 2
    assert "source mismatch" in captured.err.lower()
    assert "source path differs" in captured.err.lower()
    store = SQLiteStore(store_dir / "project.sqlite3")
    projects = store.list_projects()
    assert len(projects) == 1
    assert projects[0].source_uri == str(first.resolve())


def test_process_rejects_source_content_changed_after_import(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"

    assert run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory()) == 0
    capsys.readouterr()
    Image.new("RGB", (81, 120), "white").save(book)

    code = run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())

    captured = capsys.readouterr()
    assert code == 2
    assert "source sha-256 differs" in captured.err.lower()
    assert "evidence was not modified" in captured.err.lower()


def test_process_resolves_relative_source_paths_before_persisting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = _book(tmp_path)
    monkeypatch.chdir(tmp_path)
    store_dir = tmp_path / "store"

    code = run(["process", "book.png", "--store", str(store_dir)], engine_factory=_factory())

    assert code == 0
    project = SQLiteStore(store_dir / "project.sqlite3").list_projects()[0]
    assert project.source_uri == str(book.resolve())


def test_process_command_fails_when_runtime_not_installed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    book = _book(tmp_path)

    code = run(
        [
            "process",
            str(book),
            "--store",
            str(tmp_path / "store"),
            "--runtime-root",
            str(tmp_path / "runtime"),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "not ready" in captured.err.lower()


def test_status_reports_jobs_after_processing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(["status", "--store", str(store_dir)])

    captured = capsys.readouterr()
    assert code == 0
    assert "project 1: book" in captured.out
    assert "pages: 1" in captured.out
    assert "primary_ocr [page 0] succeeded" in captured.out
    assert "review queued: 0" in captured.out

def test_status_json_reports_page_progress_and_review_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    assert run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(),
    ) == 0
    capsys.readouterr()

    code = run(["status", "--store", str(store_dir), "--json"])

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["store"] == str(store_dir)
    assert len(payload["projects"]) == 1
    project = payload["projects"][0]
    assert project["title"] == "book"
    assert project["pages_total"] == 1
    assert project["pages_with_both_ocr_success"] == 1
    assert project["review_queued"] == 0
    assert project["job_counts"]["failed"] == 0
    assert project["failed_jobs"] == []


def test_status_json_includes_failed_job_details(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    assert run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(secondary=False),
    ) == 1
    capsys.readouterr()

    code = run(["status", "--store", str(store_dir), "--json"])

    captured = capsys.readouterr()
    assert code == 0
    project = json.loads(captured.out)["projects"][0]
    assert project["pages_total"] == 1
    assert project["pages_with_both_ocr_success"] == 0
    assert project["job_counts"]["failed"] == 1
    assert project["failed_jobs"][0]["stage"] == "secondary_ocr"
    assert project["failed_jobs"][0]["page_index"] == 0
    assert project["failed_jobs"][0]["last_error"] == "engine crashed"


def test_status_json_emits_empty_project_collection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    SQLiteStore(store_dir / "project.sqlite3").initialize()

    code = run(["status", "--store", str(store_dir), "--json"])

    captured = capsys.readouterr()
    assert code == 0
    assert json.loads(captured.out)["projects"] == []


def test_status_handles_store_without_projects(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store_dir = tmp_path / "store"
    SQLiteStore(store_dir / "project.sqlite3").initialize()

    code = run(["status", "--store", str(store_dir)])

    captured = capsys.readouterr()
    assert code == 0
    assert "no projects" in captured.out.lower()


def test_status_rejects_missing_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(["status", "--store", str(tmp_path / "missing")])

    captured = capsys.readouterr()
    assert code == 2
    assert "no store found" in captured.err.lower()


def test_main_entrypoint_returns_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["scribeforge", "status", "--store", str(tmp_path / "missing")]
    )

    assert main() == 2


def test_default_engine_factory_builds_ready_runtime_engines(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    layout = RuntimeLayout(root=runtime_root)
    os_name = platform.system().lower()
    for backend in ("mineru", "paddleocr"):
        python = layout.python_executable(backend, os_name)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
    state = {
        "schema_version": 1,
        "backends": {
            "mineru": {"state": "ready"},
            "paddleocr": {"state": "ready"},
        },
    }
    (runtime_root / "runtime-state.json").write_text(json.dumps(state), encoding="utf-8")

    engines = default_engine_factory(runtime_root)

    assert engines.primary.name == "mineru"
    assert engines.secondary.name == "paddleocr"
    assert engines.reviewer is None


def test_review_lists_queued_pairs_with_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    )
    capsys.readouterr()

    code = run(["review", "--store", str(store_dir)])

    captured = capsys.readouterr()
    assert code == 0
    assert "review queued: 1" in captured.out
    assert "page 0 pair 0" in captured.out
    assert 'primary="鸦"' in captured.out
    assert 'secondary="雅"' in captured.out
    assert 'vlm="maybe" uncertain=true' in captured.out
    assert "page-000001-pair-0000.png" in captured.out


def test_review_shows_missing_vlm_and_crop_without_a_reviewer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(reviewer=None),
    )
    capsys.readouterr()

    code = run(["review", "--store", str(store_dir)])

    captured = capsys.readouterr()
    assert code == 0
    assert "vlm=none" in captured.out
    assert "crop=none" in captured.out


def test_review_handles_an_empty_queue(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(["review", "--store", str(store_dir)])

    captured = capsys.readouterr()
    assert code == 0
    assert "review queued: 0" in captured.out


def test_review_handles_a_store_without_projects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    SQLiteStore(store_dir / "project.sqlite3").initialize()

    code = run(["review", "--store", str(store_dir)])

    captured = capsys.readouterr()
    assert code == 0
    assert "no projects" in captured.out.lower()


def test_review_rejects_missing_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(["review", "--store", str(tmp_path / "missing")])

    captured = capsys.readouterr()
    assert code == 2
    assert "no store found" in captured.err.lower()


def test_resolve_records_and_clears_a_human_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    )
    capsys.readouterr()

    code = run(
        ["resolve", "--store", str(store_dir), "--page", "0", "--pair", "0", "--text", "鸦"]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "recorded human decision" in captured.out

    store = SQLiteStore(store_dir / "project.sqlite3")
    project_id = store.list_projects()[0].id
    latest = store.latest_decision(project_id, 0, 0)
    assert latest is not None
    assert latest.selected_text == "鸦"
    assert latest.actor == "human"

    assert run(["review", "--store", str(store_dir)]) == 0
    assert "review queued: 0" in capsys.readouterr().out


def test_resolve_rejects_unknown_pair(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        ["resolve", "--store", str(store_dir), "--page", "0", "--pair", "9", "--text", "x"]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "unknown pair" in captured.err.lower()


def test_resolve_rejects_missing_store(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = run(
        ["resolve", "--store", str(tmp_path / "missing"), "--page", "0", "--pair", "0", "--text", "x"]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "no store found" in captured.err.lower()


def test_resolve_rejects_a_store_without_projects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    SQLiteStore(store_dir / "project.sqlite3").initialize()

    code = run(
        ["resolve", "--store", str(store_dir), "--page", "0", "--pair", "0", "--text", "x"]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "no projects" in captured.err.lower()


def test_export_writes_an_epub_from_decided_content(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        ["export", "--store", str(store_dir), "--out", str(out_path), "--language", "zh"]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "exported" in captured.out.lower()
    with zipfile.ZipFile(out_path) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
    assert "<dc:language>zh</dc:language>" in opf
    assert "<dc:title>book</dc:title>" in opf


def test_export_candidates_keeps_raw_engine_outputs_separate_from_verified_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()
    out_dir = tmp_path / "candidates"

    code = run(
        [
            "export-candidates",
            "--store",
            str(store_dir),
            "--out-dir",
            str(out_dir),
            "--engine",
            "primary",
            "--engine",
            "secondary",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "unverified primary candidate" in captured.out
    manifest = json.loads((out_dir / "candidate-manifest.json").read_text(encoding="utf-8"))
    assert manifest["verification_status"] == "unverified"
    assert [item["engine"] for item in manifest["candidates"]] == ["primary", "secondary"]
    assert manifest["candidates"][0]["page_runs"][0]["run_id"] > 0
    with zipfile.ZipFile(out_dir / "candidate-primary.epub") as archive:
        package = archive.read("OEBPS/content.opf").decode("utf-8")
        page = archive.read("OEBPS/page-000001.xhtml").decode("utf-8")
    assert "UNVERIFIED OCR CANDIDATE" in package
    assert "<p>鸦</p>" in page
    with zipfile.ZipFile(out_dir / "candidate-secondary.epub") as archive:
        page = archive.read("OEBPS/page-000001.xhtml").decode("utf-8")
    assert "<p>雅</p>" in page

    verified_path = tmp_path / "verified.epub"
    assert run(["export", "--store", str(store_dir), "--out", str(verified_path)]) == 0
    with zipfile.ZipFile(verified_path) as archive:
        verified_page = archive.read("OEBPS/page-000001.xhtml").decode("utf-8")
    assert "<p>verified</p>" in verified_page
    assert "<p>鸦</p>" not in verified_page


def test_export_candidates_fails_without_all_requested_engine_pages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()
    out_dir = tmp_path / "candidates"

    code = run(
        [
            "export-candidates",
            "--store",
            str(store_dir),
            "--out-dir",
            str(out_dir),
            "--engine",
            "mineru",
            "--engine",
            "secondary",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "could not build every OCR candidate" in captured.err
    assert not (out_dir / "candidate-secondary.epub").exists()
    assert not (out_dir / "candidate-manifest.json").exists()


def test_export_blocks_on_unresolved_pairs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    )
    capsys.readouterr()

    code = run(["export", "--store", str(store_dir), "--out", str(out_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "unresolved" in captured.err.lower()
    assert not out_path.exists()


def test_export_rejects_missing_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(
        ["export", "--store", str(tmp_path / "missing"), "--out", str(tmp_path / "o.epub")]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "no store found" in captured.err.lower()


def test_export_rejects_a_store_without_projects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    SQLiteStore(store_dir / "project.sqlite3").initialize()

    code = run(["export", "--store", str(store_dir), "--out", str(tmp_path / "o.epub")])

    captured = capsys.readouterr()
    assert code == 2
    assert "no projects" in captured.err.lower()


def test_export_rejects_a_project_without_pages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()
    store.create_project("Empty", "book.pdf", "sha256")

    code = run(["export", "--store", str(store_dir), "--out", str(tmp_path / "o.epub")])

    captured = capsys.readouterr()
    assert code == 2
    assert "no pages" in captured.err.lower()


def test_export_rejects_an_invalid_language(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(tmp_path / "o.epub"),
            "--language",
            "zh zh",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "language" in captured.err.lower()


def test_setup_command_opens_the_runtime_wizard_with_requested_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    import types

    seen: list[Path] = []

    def fake_setup(root: Path) -> int:
        seen.append(root)
        return 0

    module = types.ModuleType("scribeforge.ui.runtime_window")
    module.run_runtime_setup = fake_setup
    monkeypatch.setitem(sys.modules, "scribeforge.ui.runtime_window", module)

    runtime_root = tmp_path / "private-runtime"
    assert run(["setup", "--runtime-root", str(runtime_root)]) == 0
    assert seen == [runtime_root]


def test_ui_command_requires_an_existing_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(["ui", "--store", str(tmp_path / "missing")])

    captured = capsys.readouterr()
    assert code == 2
    assert "no store found" in captured.err


def test_ui_command_dispatches_to_the_desktop_shell(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()
    seen: list[Path | None] = []

    def fake(store: SQLiteStore, *, crops_dir: Path | None = None) -> int:
        seen.append(crops_dir)
        return 0

    monkeypatch.setattr("scribeforge.ui.app.run_ui", fake)

    code = run(["ui", "--store", str(store_dir)])

    assert code == 0
    assert seen == [store_dir / "crops"]


def test_export_rejects_text_xml_cannot_represent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(text="bad \x0b text"),
    )
    capsys.readouterr()

    code = run(["export", "--store", str(store_dir), "--out", str(out_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "U+000B" in captured.err
    assert not out_path.exists()


def test_export_writes_docx_for_a_docx_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.docx"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        ["export", "--store", str(store_dir), "--out", str(out_path), "--author", "Ada Lovelace"]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "exported" in captured.out.lower()
    with zipfile.ZipFile(out_path) as archive:
        assert archive.namelist()[0] == "[Content_Types].xml"
        assert "word/document.xml" in archive.namelist()
        core = archive.read("docProps/core.xml").decode("utf-8")
    assert "<dc:creator>Ada Lovelace</dc:creator>" in core


def test_export_overrides_title_and_author(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--title",
            "My Book",
            "--author",
            "Ada Lovelace",
        ]
    )

    assert code == 0
    with zipfile.ZipFile(out_path) as archive:
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
    assert "<dc:title>My Book</dc:title>" in opf
    assert "<dc:creator>Ada Lovelace</dc:creator>" in opf


def test_export_accepts_an_explicit_modified_timestamp(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--modified",
            "2020-01-02T03:04:05Z",
        ]
    )

    assert code == 0
    with zipfile.ZipFile(out_path) as archive:
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
    assert "2020-01-02T03:04:05Z" in opf


def test_export_treats_a_naive_modified_timestamp_as_utc(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--modified",
            "2020-01-02T03:04:05",
        ]
    )

    assert code == 0
    with zipfile.ZipFile(out_path) as archive:
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
    assert "2020-01-02T03:04:05Z" in opf


def test_export_rejects_an_invalid_modified_timestamp(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--modified",
            "not-a-date",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "timestamp" in captured.err.lower()
    assert not out_path.exists()


def test_export_rejects_a_blank_title(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        ["export", "--store", str(store_dir), "--out", str(out_path), "--title", "   "]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "title" in captured.err.lower()
    assert not out_path.exists()


def test_export_rejects_an_unknown_extension(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.txt"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(["export", "--store", str(store_dir), "--out", str(out_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "unsupported" in captured.err.lower()
    assert not out_path.exists()


def _corpus(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "corpus"
    root.mkdir()
    (root / "page.png").write_bytes(b"image")
    (root / "truth.txt").write_text("鸦\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "zh-1",
                        "image": "page.png",
                        "ground_truth": "truth.txt",
                        "language": "zh",
                        "tags": ["clear"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root, manifest


def _benchmark_factory(
    texts: dict[str, str] | None = None,
) -> object:
    chosen = {"mineru": "鸦", "paddleocr": "雅"} if texts is None else texts

    def factory(runtime_root: Path, backends: Sequence[str]) -> tuple[FakeEngine, ...]:
        return tuple(FakeEngine(name, chosen[name]) for name in backends)

    return factory


def test_benchmark_requires_a_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(tmp_path / "missing.json"),
            "--out",
            str(out_path),
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "manifest not found" in captured.err
    assert not out_path.exists()


def test_benchmark_rejects_an_invalid_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    out_path = tmp_path / "report.md"

    code = run(["benchmark", "--manifest", str(manifest), "--out", str(out_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "invalid manifest" in captured.err
    assert not out_path.exists()


def test_benchmark_reports_corpus_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "zh-1",
                        "image": "missing.png",
                        "ground_truth": "missing.txt",
                        "language": "zh",
                        "tags": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "report.md"

    code = run(
        ["benchmark", "--manifest", str(manifest), "--out", str(out_path)],
        benchmark_engine_factory=_benchmark_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "case zh-1" in captured.err
    assert "missing.png" in captured.err
    assert not out_path.exists()


def test_benchmark_writes_a_report_for_every_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, manifest = _corpus(tmp_path)
    out_path = tmp_path / "report.md"

    code = run(
        ["benchmark", "--manifest", str(manifest), "--out", str(out_path)],
        benchmark_engine_factory=_benchmark_factory(),
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "2 variant(s)" in captured.out
    text = out_path.read_text(encoding="utf-8")
    assert "- corpus:" in text
    assert "- manifest sha256: `" in text
    assert "| mineru |" in text
    assert "| paddleocr |" in text
    assert "| zh-1 | zh | clear |" in text


def test_benchmark_selects_a_named_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, manifest = _corpus(tmp_path)
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out_path),
            "--engine",
            "paddleocr",
        ],
        benchmark_engine_factory=_benchmark_factory(),
    )

    assert code == 0
    text = out_path.read_text(encoding="utf-8")
    assert "| paddleocr |" in text
    assert "| mineru |" not in text


def test_benchmark_rejects_an_unknown_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, manifest = _corpus(tmp_path)
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out_path),
            "--engine",
            "surya",
        ],
        benchmark_engine_factory=_benchmark_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "unknown benchmark engine: surya" in captured.err
    assert not out_path.exists()


def test_benchmark_reports_a_missing_backend(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, manifest = _corpus(tmp_path)
    out_path = tmp_path / "report.md"

    def factory(runtime_root: Path, backends: Sequence[str]) -> tuple[FakeEngine, ...]:
        raise RuntimeError("backend mineru is not ready (state=missing)")

    code = run(
        ["benchmark", "--manifest", str(manifest), "--out", str(out_path)],
        benchmark_engine_factory=factory,
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "not ready" in captured.err
    assert not out_path.exists()


def test_benchmark_reports_an_engine_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, manifest = _corpus(tmp_path)
    out_path = tmp_path / "report.md"

    def factory(runtime_root: Path, backends: Sequence[str]) -> tuple[FakeEngine, ...]:
        return (ExplodingEngine("mineru"),)

    code = run(
        ["benchmark", "--manifest", str(manifest), "--out", str(out_path)],
        benchmark_engine_factory=factory,
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "benchmark run failed" in captured.err
    assert not out_path.exists()


def test_benchmark_reports_an_unwritable_destination(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, manifest = _corpus(tmp_path)

    code = run(
        ["benchmark", "--manifest", str(manifest), "--out", str(tmp_path)],
        benchmark_engine_factory=_benchmark_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "cannot write report" in captured.err


def test_default_benchmark_engine_factory_builds_only_requested_backends(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime"
    layout = RuntimeLayout(root=runtime_root)
    os_name = platform.system().lower()
    for backend in ("mineru", "paddleocr"):
        python = layout.python_executable(backend, os_name)
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"")
    state = {
        "schema_version": 1,
        "backends": {
            "mineru": {"state": "ready"},
            "paddleocr": {"state": "ready"},
        },
    }
    (runtime_root / "runtime-state.json").write_text(json.dumps(state), encoding="utf-8")

    engines = default_benchmark_engine_factory(runtime_root, ("paddleocr",))

    assert [engine.name for engine in engines] == ["paddleocr"]


def test_default_benchmark_engine_factory_rejects_an_unknown_backend(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="unknown benchmark engine"):
        default_benchmark_engine_factory(tmp_path / "runtime", ("surya",))



def test_relink_source_accepts_identical_bytes_and_preserves_existing_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = _book(tmp_path)
    moved = tmp_path / "moved-book.png"
    moved.write_bytes(original.read_bytes())
    store_dir = tmp_path / "book-store"

    assert run(["process", str(original), "--store", str(store_dir)], engine_factory=_factory()) == 0
    store = SQLiteStore(store_dir / "project.sqlite3")
    before = store.list_projects()[0]
    before_pages = store.list_pages(before.id)
    before_run_id = store.latest_engine_run(before.id, 0, "primary")
    before_result = store.load_ocr_run(before_run_id)
    capsys.readouterr()

    code = run(
        ["relink-source", "--store", str(store_dir), "--source", str(moved)],
    )

    captured = capsys.readouterr()
    after = store.list_projects()[0]
    assert code == 0
    assert "SHA-256 unchanged" in captured.out
    assert after.source_uri == str(moved.resolve())
    assert after.source_sha256 == before.source_sha256
    assert after.title == before.title
    assert store.list_pages(after.id) == before_pages
    assert store.latest_engine_run(after.id, 0, "primary") == before_run_id
    assert store.load_ocr_run(before_run_id) == before_result


def test_relink_source_rejects_different_content_without_modifying_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    original = _book(tmp_path)
    changed = tmp_path / "changed-book.png"
    Image.new("RGB", (82, 121), "black").save(changed)
    store_dir = tmp_path / "book-store"

    assert run(["process", str(original), "--store", str(store_dir)], engine_factory=_factory()) == 0
    store = SQLiteStore(store_dir / "project.sqlite3")
    before = store.list_projects()[0]
    before_pages = store.list_pages(before.id)
    capsys.readouterr()

    code = run(
        ["relink-source", "--store", str(store_dir), "--source", str(changed)],
    )

    captured = capsys.readouterr()
    after = store.list_projects()[0]
    assert code == 2
    assert "sha-256 does not match" in captured.err.lower()
    assert "not modified" in captured.err.lower()
    assert after == before
    assert store.list_pages(after.id) == before_pages


def test_relink_source_rejects_missing_source_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()
    store.create_project("Book", str(tmp_path / "old.pdf"), "expected-hash")

    code = run(
        [
            "relink-source",
            "--store",
            str(store_dir),
            "--source",
            str(tmp_path / "missing.pdf"),
        ],
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "source file not found" in captured.err.lower()
    assert store.list_projects()[0].source_uri == str(tmp_path / "old.pdf")


def test_relink_source_rejects_multi_project_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _book(tmp_path)
    store_dir = tmp_path / "multi-store"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()
    store.create_project("One", str(source.resolve()), "hash-one")
    store.create_project("Two", str(source.resolve()), "hash-two")

    code = run(
        ["relink-source", "--store", str(store_dir), "--source", str(source)],
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "multiple projects" in captured.err.lower()
    assert [project.title for project in store.list_projects()] == ["One", "Two"]



def test_runtime_status_json_reports_managed_backend_state_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    state_path = runtime_root / "runtime-state.json"
    state = {
        "schema_version": 1,
        "backends": {
            "mineru": {"state": "ready", "failed_step": None},
            "paddleocr": {"state": "failed", "failed_step": "models:download"},
        },
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()
    monkeypatch.setattr(runtime_probe, "_nvidia_profile", lambda: None)
    monkeypatch.setattr(runtime_probe, "_ram_mb", lambda: 16 * 1024)

    code = run(
        [
            "runtime-status",
            "--runtime-root",
            str(runtime_root),
            "--json",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    report = json.loads(captured.out)
    assert report["schema_version"] == 1
    assert report["runtime_root"] == str(runtime_root)
    assert report["runtime_root_exists"] is True
    assert report["hardware"]["gpu_name"] is None
    assert report["hardware"]["ram_mb"] == 16 * 1024
    assert report["backends"]["mineru"]["state"] == "ready"
    assert report["backends"]["paddleocr"]["state"] == "failed"
    assert report["backends"]["paddleocr"]["failed_step"] == "models:download"
    assert report["paths"]["models"] == str(runtime_root / "models")
    assert state_path.read_bytes() == before


def test_runtime_status_json_reports_missing_backends(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime-missing"
    monkeypatch.setattr(runtime_probe, "_nvidia_profile", lambda: None)
    monkeypatch.setattr(runtime_probe, "_ram_mb", lambda: None)

    code = run(
        ["runtime-status", "--runtime-root", str(runtime_root), "--json"]
    )

    captured = capsys.readouterr()
    assert code == 0
    report = json.loads(captured.out)
    assert report["runtime_root_exists"] is False
    assert report["backends"]["mineru"]["state"] == "missing"
    assert report["backends"]["paddleocr"]["state"] == "missing"
    assert report["backends"]["mineru"]["environment_exists"] is False



def test_benchmark_include_pipeline_reports_conservative_unresolved_pairs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "corpus"
    root.mkdir()
    Image.new("RGB", (80, 120), "white").save(root / "page.png")
    (root / "truth.txt").write_text("鸦", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "zh-1",
                        "image": "page.png",
                        "ground_truth": "truth.txt",
                        "language": "zh",
                        "tags": ["clear"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out_path),
            "--include-pipeline",
        ],
        benchmark_engine_factory=_benchmark_factory(),
    )

    report = out_path.read_text(encoding="utf-8")
    assert code == 0
    assert "### dual-engine" in report
    assert "Accepted pairs" in report
    assert "Unresolved pairs" in report
    assert "| zh-1 | zh | clear | 1.0000 |" in report
    assert "| 1 | 0 |" in report


def test_benchmark_pipeline_requires_both_backends(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, manifest = _corpus(tmp_path)
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out_path),
            "--engine",
            "mineru",
            "--include-pipeline",
        ],
        benchmark_engine_factory=_benchmark_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "requires both mineru and paddleocr" in captured.err.lower()
    assert not out_path.exists()
