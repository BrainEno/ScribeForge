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
from scribeforge.pipeline.orchestrator import process_book
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
    """A reviewer that corroborates the primary engine's reading.

    A vision model's answer is only accepted when it agrees with one of the
    engines (issue #76), so a fake that invented a third text would leave every
    pair unresolved and test the wrong path.
    """

    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading:
        return VLMReading(page_index, pair_index, "fake-vlm", "1.0", "鸦", False)


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


def test_status_json_exposes_the_running_page_and_stage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    assert run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(),
    ) == 0
    store = SQLiteStore(store_dir / "project.sqlite3")
    project = store.list_projects()[0]
    store.add_page(project.id, 1, str(book.resolve()), 80, 120)
    job_id = store.ensure_job(project.id, JobStage.PRIMARY_OCR, page_index=1)
    store.start_job(job_id)
    capsys.readouterr()

    code = run(["status", "--store", str(store_dir), "--json"])

    captured = capsys.readouterr()
    assert code == 0
    project_json = json.loads(captured.out)["projects"][0]
    assert project_json["active_jobs"] == [
        {"stage": "primary_ocr", "page_index": 1, "attempts": 1}
    ]


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


def test_review_json_returns_machine_readable_evidence_and_page_assets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    assert run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    ) == 0
    capsys.readouterr()

    code = run(["review", "--store", str(store_dir), "--json"])

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    project = payload["projects"][0]
    assert project["id"] == 1
    assert project["title"] == "book"
    assert project["source_uri"] == str(book.resolve())
    item = project["items"][0]
    assert item["page_index"] == 0
    assert item["pair_index"] == 0
    assert item["score"] > 0
    assert item["reasons"]
    assert item["primary_text"] == "鸦"
    assert item["secondary_text"] == "雅"
    assert item["vlm_text"] == "maybe"
    assert item["vlm_uncertain"] is True
    assert Path(item["source_image_path"]).is_file()
    assert Path(item["crop_path"]).is_file()


def test_review_json_emits_empty_items_for_a_project_without_reviews(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    assert run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory()) == 0
    capsys.readouterr()

    code = run(["review", "--store", str(store_dir), "--json"])

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["projects"][0]["items"] == []


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


def test_review_json_emits_empty_projects_for_a_store_without_projects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "store"
    SQLiteStore(store_dir / "project.sqlite3").initialize()

    code = run(["review", "--store", str(store_dir), "--json"])

    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["store"] == str(store_dir)
    assert payload["projects"] == []


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


def test_resolve_targets_the_selected_project_in_a_multi_project_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first_book = _book(tmp_path)
    second_book = tmp_path / "second.png"
    Image.new("RGB", (80, 120), "white").save(second_book)
    store_dir = tmp_path / "store"
    assert run(
        ["process", str(first_book), "--store", str(store_dir)],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    ) == 0
    store = SQLiteStore(store_dir / "project.sqlite3")
    first_project_id = store.list_projects()[0].id
    second_project_id = store.create_project(
        "second", str(second_book.resolve()), "f" * 64
    )
    store.add_page(second_project_id, 0, str(second_book.resolve()), 80, 120)
    outcome = process_book(
        store,
        second_project_id,
        primary_engine=FakeEngine("primary", "鸦"),
        secondary_engine=FakeEngine("secondary", "雅"),
        reviewer=UncertainReviewer(),
        crops_dir=store_dir / "crops",
    )
    assert not outcome.failed_pages
    capsys.readouterr()

    code = run(
        [
            "resolve",
            "--store",
            str(store_dir),
            "--project-id",
            str(second_project_id),
            "--page",
            "0",
            "--pair",
            "0",
            "--text",
            "选择第二项目",
        ]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert f"project {second_project_id}" in captured.out
    second_decision = store.latest_decision(second_project_id, 0, 0)
    assert second_decision is not None
    assert second_decision.selected_text == "选择第二项目"
    assert store.latest_decision(first_project_id, 0, 0) is None


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
    # The verified export carries the accepted decision (the primary engine's
    # reading, corroborated by the vision model), not both raw candidates.
    assert "<p>鸦</p>" in verified_page
    assert "<p>雅</p>" not in verified_page


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


def test_setup_json_streams_progress_and_final_backend_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import types

    from scribeforge.runtime.manager import RuntimeEvent, RuntimeStatus
    from scribeforge.runtime.models import HardwareProfile

    runtime_root = tmp_path / "private-runtime"
    profile = HardwareProfile("windows", "AMD64", None, None, None, 16 * 1024)

    class FakeSetupWorkflow:
        def __init__(self, root: Path) -> None:
            assert root == runtime_root

        def install(self, *, on_event) -> None:
            on_event(RuntimeEvent("mineru", "models", "step_started", "download", 2, 5))
            on_event(RuntimeEvent("mineru", "models", "step_completed", "download", 3, 5))
            on_event(RuntimeEvent("mineru", "complete", "ready", None, 5, 5))

        def inspect(self):
            return types.SimpleNamespace(
                hardware=profile,
                uv_available=True,
                mineru=RuntimeStatus("ready"),
                paddleocr=RuntimeStatus("ready"),
            )

    module = types.ModuleType("scribeforge.runtime.workflow")
    module.RuntimeSetupWorkflow = FakeSetupWorkflow
    monkeypatch.setitem(sys.modules, "scribeforge.runtime.workflow", module)
    output = io.StringIO()

    code = run(
        ["setup", "--runtime-root", str(runtime_root), "--json"],
        stdout=output,
    )

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert code == 0
    assert [record["type"] for record in records] == ["progress", "progress", "progress", "result"]
    assert records[0] == {
        "schema_version": 1,
        "type": "progress",
        "backend": "mineru",
        "phase": "models",
        "state": "step_started",
        "step": "download",
        "completed_steps": 2,
        "total_steps": 5,
    }
    assert records[-1]["action"] == "install"
    assert records[-1]["success"] is True
    assert records[-1]["backends"]["mineru"]["state"] == "ready"
    assert records[-1]["backends"]["paddleocr"]["state"] == "ready"


def test_setup_json_repairs_only_requested_backend_and_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import types

    from scribeforge.runtime.manager import RuntimeEvent, RuntimeInstallError, RuntimeStatus
    from scribeforge.runtime.models import HardwareProfile

    runtime_root = tmp_path / "private-runtime"
    profile = HardwareProfile("windows", "AMD64", None, None, None, 16 * 1024)
    calls: list[str] = []

    class FakeSetupWorkflow:
        def __init__(self, root: Path) -> None:
            assert root == runtime_root

        def repair(self, backend: str, *, on_event) -> None:
            calls.append(backend)
            on_event(RuntimeEvent("paddleocr", "models", "step_started", "download", 1, 4))
            on_event(RuntimeEvent("paddleocr", "models", "failed", "download", 1, 4))
            raise RuntimeInstallError("paddleocr:models:download: network unavailable")

        def inspect(self):
            return types.SimpleNamespace(
                hardware=profile,
                uv_available=True,
                mineru=RuntimeStatus("ready"),
                paddleocr=RuntimeStatus("failed", "models:download"),
            )

    module = types.ModuleType("scribeforge.runtime.workflow")
    module.RuntimeSetupWorkflow = FakeSetupWorkflow
    monkeypatch.setitem(sys.modules, "scribeforge.runtime.workflow", module)
    output = io.StringIO()

    code = run(
        [
            "setup",
            "--runtime-root",
            str(runtime_root),
            "--backend",
            "paddleocr",
            "--json",
        ],
        stdout=output,
    )

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert code == 1
    assert calls == ["paddleocr"]
    assert records[-1]["action"] == "repair"
    assert records[-1]["success"] is False
    assert records[-1]["message"] == "paddleocr:models:download: network unavailable"
    assert records[-1]["backends"]["mineru"]["state"] == "ready"
    assert records[-1]["backends"]["paddleocr"]["failed_step"] == "models:download"


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


def test_export_reports_the_layout_it_wrote(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    epub_path = tmp_path / "out.epub"
    docx_path = tmp_path / "out.docx"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(epub_path),
            "--json",
            "--modified",
            "2024-01-01T00:00:00Z",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["layout"] == "page"
    assert report["documents"] == 1
    assert report["paragraphs"] == 1

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(docx_path),
            "--layout",
            "chapter",
            "--json",
            "--modified",
            "2024-01-01T00:00:00Z",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    # DOCX is written page by page, so reporting a layout would describe a file
    # that was never re-flowed.
    assert "layout" not in report
    assert "documents" not in report


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
            "tesseract",
        ],
        benchmark_engine_factory=_benchmark_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "unknown benchmark engine: tesseract" in captured.err
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
        default_benchmark_engine_factory(tmp_path / "runtime", ("tesseract",))



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


def test_benchmark_include_vlm_requires_pipeline_and_explicit_endpoint(
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
            "--include-vlm",
        ],
        benchmark_engine_factory=_benchmark_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "--include-vlm requires --include-pipeline" in captured.err
    assert not out_path.exists()


def test_benchmark_include_vlm_requires_endpoint_and_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, manifest = _corpus(tmp_path)
    Image.new("RGB", (80, 120), "white").save(root / "page.png")
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out_path),
            "--include-pipeline",
            "--include-vlm",
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
        ],
        benchmark_engine_factory=_benchmark_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "--vlm-model is required" in captured.err
    assert not out_path.exists()


def test_process_requires_vlm_endpoint_and_model_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    code = run(
        [
            "process",
            str(book),
            "--store",
            str(tmp_path / "store"),
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
        ],
        engine_factory=_factory(),
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "--vlm-model is required" in captured.err
    assert not (tmp_path / "store" / "project.sqlite3").exists()



def test_process_uses_explicitly_configured_vlm_reviewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scribeforge.cli.OpenAICompatibleVLMReviewer",
        lambda **_kwargs: FakeReviewer(),
    )
    book = _book(tmp_path)
    store_dir = tmp_path / "store"

    code = run(
        [
            "process",
            str(book),
            "--store",
            str(store_dir),
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
            "--vlm-model",
            "fixture-vision",
        ],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    )

    assert code == 0
    store = SQLiteStore(store_dir / "project.sqlite3")
    project = store.list_projects()[0]
    decision = store.latest_decision(project.id, 0, 0)
    assert decision is not None
    assert decision.actor == "vlm"
    assert decision.selected_text == "鸦"


def test_benchmark_include_vlm_adds_variant_d(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scribeforge.cli.OpenAICompatibleVLMReviewer",
        lambda **_kwargs: FakeReviewer(),
    )
    root, manifest = _corpus(tmp_path)
    Image.new("RGB", (80, 120), "white").save(root / "page.png")
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out_path),
            "--include-pipeline",
            "--include-vlm",
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
            "--vlm-model",
            "fixture-vision",
        ],
        benchmark_engine_factory=_benchmark_factory(),
    )

    report = out_path.read_text(encoding="utf-8")
    assert code == 0
    assert "### dual-engine\n" in report
    assert "### dual-engine+vlm" in report
    assert "| dual-engine+vlm |" in report



def test_process_requires_configured_api_key_environment_variable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSING_VLM_TOKEN", raising=False)
    book = _book(tmp_path)

    code = run(
        [
            "process",
            str(book),
            "--store",
            str(tmp_path / "store"),
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
            "--vlm-model",
            "fixture-vision",
            "--vlm-api-key-env",
            "MISSING_VLM_TOKEN",
        ],
        engine_factory=_factory(),
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "environment variable is not set" in captured.err
    assert not (tmp_path / "store" / "project.sqlite3").exists()



def test_vlm_check_runs_explicit_crop_diagnostic_and_emits_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    image = _book(tmp_path)
    calls: list[tuple[str, int, int]] = []

    class RecordingReviewer:
        def transcribe_crop(
            self, image_path: str, page_index: int, pair_index: int
        ) -> VLMReading:
            calls.append((image_path, page_index, pair_index))
            return VLMReading(page_index, pair_index, "fixture-model", "fixture-v1", "sample", False)

    monkeypatch.setattr(
        "scribeforge.cli.OpenAICompatibleVLMReviewer",
        lambda **_kwargs: RecordingReviewer(),
    )

    code = run(
        [
            "vlm-check",
            "--image",
            str(image),
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
            "--vlm-model",
            "fixture-model",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert code == 0
    assert report["schema_version"] == 1
    assert report["status"] == "transcribed"
    assert report["model"] == "fixture-model"
    assert report["model_version"] == "fixture-v1"
    assert report["text"] == "sample"
    assert report["uncertain"] is False
    assert calls == [(str(image.resolve()), 0, 0)]
    assert not (tmp_path / "project.sqlite3").exists()


def test_vlm_check_returns_partial_for_uncertain_or_blank_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    image = _book(tmp_path)
    monkeypatch.setattr(
        "scribeforge.cli.OpenAICompatibleVLMReviewer",
        lambda **_kwargs: UncertainReviewer(),
    )

    code = run(
        [
            "vlm-check",
            "--image",
            str(image),
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
            "--vlm-model",
            "fixture-model",
        ]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "uncertain" in captured.out
    assert "maybe" in captured.out


def test_vlm_check_rejects_missing_image_before_constructing_reviewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unexpected_constructor(**_kwargs: object) -> FakeReviewer:
        pytest.fail("reviewer must not be configured or called when the image is missing")

    monkeypatch.setattr(
        "scribeforge.cli.OpenAICompatibleVLMReviewer", unexpected_constructor
    )

    code = run(
        [
            "vlm-check",
            "--image",
            str(tmp_path / "missing.png"),
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
            "--vlm-model",
            "fixture-model",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "image not found" in captured.err.lower()


def test_vlm_check_requires_explicit_endpoint_and_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image = _book(tmp_path)

    code = run(["vlm-check", "--image", str(image)])

    captured = capsys.readouterr()
    assert code == 2
    assert "vlm-endpoint and --vlm-model are required" in captured.err



def test_vlm_check_fails_closed_when_the_endpoint_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    image = _book(tmp_path)

    class BrokenReviewer:
        def transcribe_crop(
            self, image_path: str, page_index: int, pair_index: int
        ) -> VLMReading:
            raise RuntimeError("synthetic endpoint timeout")

    monkeypatch.setattr(
        "scribeforge.cli.OpenAICompatibleVLMReviewer",
        lambda **_kwargs: BrokenReviewer(),
    )

    code = run(
        [
            "vlm-check",
            "--image",
            str(image),
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
            "--vlm-model",
            "fixture-model",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "VLM check failed" in captured.err
    assert "synthetic endpoint timeout" in captured.err


def test_vlm_check_reports_blank_text_as_partial_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    image = _book(tmp_path)

    class BlankReviewer:
        def transcribe_crop(
            self, image_path: str, page_index: int, pair_index: int
        ) -> VLMReading:
            return VLMReading(page_index, pair_index, "fixture-model", "fixture-v1", "  ", False)

    monkeypatch.setattr(
        "scribeforge.cli.OpenAICompatibleVLMReviewer",
        lambda **_kwargs: BlankReviewer(),
    )

    code = run(
        [
            "vlm-check",
            "--image",
            str(image),
            "--vlm-endpoint",
            "http://127.0.0.1:1234/v1",
            "--vlm-model",
            "fixture-model",
            "--json",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report["status"] == "blank"
    assert report["text"] == "  "
    assert report["uncertain"] is False



def test_benchmark_rejects_manifest_paths_that_escape_corpus_root_before_starting_engines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _root, manifest = _corpus(tmp_path)
    (tmp_path / "outside.png").write_bytes(b"image")
    (tmp_path / "outside.txt").write_text("outside text", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["cases"][0]["image"] = "../outside.png"
    data["cases"][0]["ground_truth"] = "../outside.txt"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    out_path = tmp_path / "report.md"

    def should_not_start_engine(
        runtime_root: Path, backends: Sequence[str]
    ) -> tuple[FakeEngine, ...]:
        pytest.fail("unsafe corpus paths must be rejected before constructing OCR engines")

    code = run(
        ["benchmark", "--manifest", str(manifest), "--out", str(out_path)],
        benchmark_engine_factory=should_not_start_engine,
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "image path escapes corpus root" in captured.err
    assert "ground truth path escapes corpus root" in captured.err
    assert not out_path.exists()


class CropAwareEngine(FakeEngine):
    """A worker that reads an isolated line better than it read the whole page."""

    def __init__(self, name: str, page_text: str, crop_text: str) -> None:
        super().__init__(name, page_text)
        self._crop_text = crop_text
        self.crop_calls = 0

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        # A region re-read is recognisable by the attempt number in its file name;
        # matching on a directory name would also match pytest's tmp_path.
        if "-read-" in Path(image_path).name:
            self.crop_calls += 1
            box = BoundingBox(0.1, 0.1, 0.8, 0.02)
            return PageOCRResult(
                page_index, self._name, "1.0", (OCRLine(self._crop_text, box),)
            )
        return super().analyze_page(image_path, page_index)


def _crop_aware_factory(
    crop_text: str, secondary_crop_text: str | None = None
) -> tuple[object, list[CropAwareEngine]]:
    engines: list[CropAwareEngine] = []

    def factory(runtime_root: Path) -> Engines:
        primary = CropAwareEngine("primary", "鸦", crop_text)
        secondary = CropAwareEngine(
            "secondary", "雅", crop_text if secondary_crop_text is None else secondary_crop_text
        )
        engines.extend((primary, secondary))
        return Engines(primary=primary, secondary=secondary, reviewer=None)

    return factory, engines


def test_process_with_repair_rounds_settles_a_dispute_without_a_person(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    factory, engines = _crop_aware_factory("鸦")

    code = run(
        [
            "process",
            str(book),
            "--store",
            str(store_dir),
            "--repair-rounds",
            "2",
        ],
        engine_factory=factory,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "repair round 1" in captured.out
    assert "resolved 1" in captured.out
    assert "0 pair(s) need review" in captured.out
    assert "accepted without a person: 1 automatic" in captured.out
    assert all(engine.crop_calls == 1 for engine in engines)

    store = SQLiteStore(store_dir / "project.sqlite3")
    project_id = store.list_projects()[0].id
    decision = store.latest_decision(project_id, 0, 0)
    assert decision is not None and decision.selected_text == "鸦"


def test_repair_command_reports_remaining_pairs_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    )
    capsys.readouterr()
    # The two re-reads disagree with each other and with both engines: no
    # two readers support the same text, so the pair must stay open.
    factory, _ = _crop_aware_factory("鸡", "蛙")

    code = run(
        ["repair", "--store", str(store_dir), "--repair-rounds", "1", "--json"],
        engine_factory=factory,
    )

    captured = capsys.readouterr()
    assert code == 1
    report = json.loads(captured.out)
    assert report["rounds"][0]["examined"] == 1
    assert report["rounds"][0]["resolved"] == 0
    assert report["progress"]["open_pairs"] == 1
    assert report["progress"]["auto_acceptance"] == 0.0


def test_repair_command_settles_and_can_then_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_factory(reviewer=UncertainReviewer()),
    )
    capsys.readouterr()
    factory, _ = _crop_aware_factory("鸦")

    code = run(
        ["repair", "--store", str(store_dir), "--repair-rounds", "1"],
        engine_factory=factory,
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "accepted without a person: 1 automatic" in captured.out

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(tmp_path / "book.epub"),
            "--title",
            "Book",
        ]
    )

    assert code == 0
    with zipfile.ZipFile(tmp_path / "book.epub") as archive:
        page = archive.read("OEBPS/page-000001.xhtml").decode()
    assert "鸦" in page


def test_repair_command_rejects_a_missing_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run(["repair", "--store", str(tmp_path / "nope")])

    captured = capsys.readouterr()
    assert code == 2
    assert "no store found" in captured.err


class BlankEngine:
    """An engine that sees a page and reports no text at all."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        return PageOCRResult(page_index, self._name, "1.0", ())


class LowConfidenceEngine(FakeEngine):
    """An engine that agrees with its twin but reports middling confidence.

    0.5 is below the "worth a second look" line and above the acceptance floor, so
    the pair is exportable but flagged — the case the spot check exists for.
    """

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        token = OCRToken(self._text, 0.5, box)
        return PageOCRResult(
            page_index, self.name, "1.0", (OCRLine(self._text, box, (token,)),)
        )


def _custom_factory(primary, secondary, reviewer: object = None):
    def factory(runtime_root: Path) -> Engines:
        return Engines(primary=primary, secondary=secondary, reviewer=reviewer)

    return factory


def _book_with_ink(tmp_path: Path) -> Path:
    """A page image that genuinely has something on it (a plate, not a leaf)."""

    book = tmp_path / "plate.png"
    image = Image.new("RGB", (80, 120), "white")
    for x in range(10, 70):
        for y in range(20, 100):
            image.putpixel((x, y), (20, 20, 20))
    image.save(book)
    return book


def test_a_measured_blank_page_exports_without_a_person(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A page with no ink at all is settled by measurement, not by a click.

    This is the #74 case that *can* be automated: the engines read nothing and the
    raster has nothing to read. Requiring a human to confirm that would make a
    562-page book's blank leaves a manual chore for no added safety.
    """

    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(BlankEngine("primary"), BlankEngine("secondary")),
    )
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--json",
            "--modified",
            "2024-01-01T00:00:00Z",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["confirmed_blank_pages"] == [0]
    store = SQLiteStore(store_dir / "project.sqlite3")
    project = store.list_projects()[0]
    confirmations = store.list_page_confirmations(project.id, 0)
    assert [(item.kind, item.actor) for item in confirmations] == [("blank", "evidence")]
    assert confirmations[0].ink_ratio == 0.0


def test_export_refuses_a_page_with_ink_but_no_text_until_it_is_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression for #74: a page nobody could read must not pass as a blank leaf.

    The page has ink on it, so "the engines read nothing" is a failure, not a blank
    leaf. Export refuses, and the operator has to say what the page is.
    """

    book = _book_with_ink(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(BlankEngine("primary"), BlankEngine("secondary")),
    )
    capsys.readouterr()

    code = run(["export", "--store", str(store_dir), "--out", str(out_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "not yet confirmed as textless" in captured.err
    assert "scribeforge confirm --page N" in captured.err
    assert not out_path.exists()

    confirm_code = run(
        [
            "confirm",
            "--store",
            str(store_dir),
            "--page",
            "0",
            "--kind",
            "non_text",
            "--reason",
            "frontispiece plate, no caption",
        ]
    )
    assert confirm_code == 0
    export_code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--modified",
            "2024-01-01T00:00:00Z",
        ]
    )
    assert export_code == 0
    assert out_path.exists()


def test_export_embeds_the_cover_and_reports_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A confirmed picture must reach the reader, and the report must say so.

    The fixture book is a single page of artwork, so it is the cover: the first
    page of a scan that carries no text is the one page a reading system should
    show in a library grid.
    """

    book = _book_with_ink(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(BlankEngine("primary"), BlankEngine("secondary")),
    )
    run(
        [
            "confirm",
            "--store",
            str(store_dir),
            "--page",
            "0",
            "--kind",
            "non_text",
            "--reason",
            "frontispiece plate, no caption",
        ]
    )
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--json",
            "--modified",
            "2024-01-01T00:00:00Z",
        ]
    )

    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert code == 0
    assert report["images"] == 1
    assert report["cover_image"]["page"] == 0
    assert report["cover_image"]["media_type"] == "image/png"
    with zipfile.ZipFile(out_path) as archive:
        images = [name for name in archive.namelist() if name.startswith("OEBPS/images/")]
        package = archive.read("OEBPS/content.opf").decode("utf-8")
    assert images == ["OEBPS/images/page-000001.png"]
    assert 'properties="cover-image"' in package
    assert '<meta name="cover" content="image-page-0"/>' in package


def test_export_warns_that_docx_does_not_carry_page_images(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book_with_ink(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.docx"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(BlankEngine("primary"), BlankEngine("secondary")),
    )
    run(
        [
            "confirm",
            "--store",
            str(store_dir),
            "--page",
            "0",
            "--kind",
            "non_text",
            "--reason",
            "frontispiece plate, no caption",
        ]
    )
    capsys.readouterr()

    code = run(["export", "--store", str(store_dir), "--out", str(out_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert "page images: 1 embedded" in captured.out
    assert "DOCX export writes text only" in captured.err


def test_confirm_makes_a_textless_page_exportable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(BlankEngine("primary"), BlankEngine("secondary")),
    )

    code = run(
        [
            "confirm",
            "--store",
            str(store_dir),
            "--page",
            "0",
            "--kind",
            "blank",
            "--reason",
            "empty leaf before the title page",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "confirmed page 0 as blank" in captured.out

    export_code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--json",
            "--modified",
            "2024-01-01T00:00:00Z",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert export_code == 0
    assert out_path.exists()
    assert report["confirmed_blank_pages"] == [0]
    assert report["accepted_pairs"] == 0
    assert report["requires_human_review"] is False


def test_confirm_rejects_an_unknown_page(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        ["confirm", "--store", str(store_dir), "--page", "7", "--reason", "blank"]
    )

    assert code == 2
    assert "no page 7" in capsys.readouterr().err


def test_confirm_requires_a_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    run(["process", str(book), "--store", str(store_dir)], engine_factory=_factory())
    capsys.readouterr()

    code = run(
        ["confirm", "--store", str(store_dir), "--page", "0", "--reason", "   "]
    )

    assert code == 2
    assert "--reason must not be empty" in capsys.readouterr().err


def test_export_reports_the_machine_accepted_share(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(
            LowConfidenceEngine("primary", "鸦"),
            LowConfidenceEngine("secondary", "鸦"),
            FakeReviewer(),
        ),
    )
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--json",
            "--modified",
            "2024-01-01T00:00:00Z",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["machine_accepted"] == 1
    assert report["human_accepted"] == 0
    assert report["requires_human_review"] is True
    assert report["spot_check"][0]["reasons"] == ["low_confidence"]
    assert report["spot_check"][0]["content_risk"] > 0


def test_strict_acceptance_refuses_an_unspot_checked_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(
            LowConfidenceEngine("primary", "鸦"),
            LowConfidenceEngine("secondary", "鸦"),
            FakeReviewer(),
        ),
    )
    capsys.readouterr()

    code = run(
        [
            "export",
            "--store",
            str(store_dir),
            "--out",
            str(out_path),
            "--strict-acceptance",
            "--modified",
            "2024-01-01T00:00:00Z",
        ]
    )

    captured = capsys.readouterr()
    assert code == 2
    assert "--strict-acceptance" in captured.err
    assert not out_path.exists()



class GuessingEngine(FakeEngine):
    """An engine that reports 0.1 confidence: it is telling us it cannot read."""

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        token = OCRToken(self._text, 0.1, box)
        return PageOCRResult(
            page_index, self.name, "1.0", (OCRLine(self._text, box, (token,)),)
        )


def test_export_names_the_reason_a_pair_is_blocked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A refusal that only says "1 unresolved pair" is not actionable (issue #75)."""

    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(
            GuessingEngine("primary", "鸦"), GuessingEngine("secondary", "鸦")
        ),
    )
    capsys.readouterr()

    code = run(["export", "--store", str(store_dir), "--out", str(out_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "page 0 pair 0" in captured.err
    assert "low_confidence" in captured.err
    assert not out_path.exists()


def test_export_reports_unrepresentable_characters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    book = _book(tmp_path)
    store_dir = tmp_path / "store"
    out_path = tmp_path / "out.epub"
    run(
        ["process", str(book), "--store", str(store_dir)],
        engine_factory=_custom_factory(
            GuessingEngine("primary", "\ufffd"), GuessingEngine("secondary", "\ufffd")
        ),
    )
    capsys.readouterr()

    code = run(["export", "--store", str(store_dir), "--out", str(out_path)])

    captured = capsys.readouterr()
    assert code == 2
    assert "unrepresentable_text" in captured.err
    assert "U+FFFD" in captured.err
    assert not out_path.exists()


def test_rehash_pages_records_digests_for_pages_without_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store_dir = tmp_path / "Book.scribeforge"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")

    assets = store_dir / "assets"
    assets.mkdir(parents=True)
    page_image = assets / "page-000001.png"
    page_image.write_bytes(b"legacy page image bytes")
    # Imported before schema v11: no page asset digest recorded.
    store.add_page(project_id, 0, str(page_image.resolve()), 100, 200)

    code = run(["rehash-pages", "--store", str(store_dir), "--json"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["rehashed"] == 1
    assert payload["missing"] == 0

    pages = store.list_pages(project_id)
    assert pages[0].page_asset_sha256 is not None
    assert pages[0].page_asset_version == 1
    assert pages[0].page_asset_byte_size == len(b"legacy page image bytes")


def test_rehash_pages_reports_missing_raster(tmp_path: Path) -> None:
    store_dir = tmp_path / "Book.scribeforge"
    store = SQLiteStore(store_dir / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    store.add_page(project_id, 0, "/gone/page-000001.png", 100, 200)

    code = run(["rehash-pages", "--store", str(store_dir)])
    assert code == 2

    pages = store.list_pages(project_id)
    assert pages[0].page_asset_sha256 is None
