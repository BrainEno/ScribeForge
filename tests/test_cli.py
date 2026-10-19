from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.cli import Engines, default_engine_factory, main, run
from scribeforge.domain.jobs import JobStage
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.vlm import VLMReading
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


def _factory(*, secondary: bool = True, reviewer: object = _DEFAULT_REVIEWER):
    chosen_reviewer = FakeReviewer() if reviewer is _DEFAULT_REVIEWER else reviewer

    def factory(runtime_root: Path) -> Engines:
        secondary_engine = (
            FakeEngine("secondary", "雅") if secondary else ExplodingEngine("secondary")
        )
        return Engines(
            primary=FakeEngine("primary", "鸦"),
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
