"""Structured JSONL event log tests (issue #69).

`process --events-file PATH` writes one JSON object per line for every
pipeline milestone (book_started / page_started / page_finished /
page_failed / book_finished / book_aborted), so a multi-hour run leaves a
machine-readable trace of where time went and which pages failed. Home
directory paths are redacted in every line.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    cancellation_scope,
)
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.pipeline.events import EventLog, event_line
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.storage.sqlite import SQLiteStore


class FakeEngine:
    def __init__(self, name: str, text: str = "鸦") -> None:
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


class FailingEngine(FakeEngine):
    def __init__(self, failing_pages: set[int]) -> None:
        super().__init__("primary")
        self._failing = failing_pages

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        if page_index in self._failing:
            raise RuntimeError(f"crash on {page_index}")
        return super().analyze_page(image_path, page_index)


class CancellingEngine(FakeEngine):
    def __init__(self, token: CancellationToken) -> None:
        super().__init__("primary")
        self._token = token

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        self._token.cancel()
        self._token.raise_if_cancelled()
        raise AssertionError("unreachable")


def _store(tmp_path: Path, page_count: int) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    for index in range(page_count):
        image_path = tmp_path / f"page-{index}.png"
        Image.new("RGB", (100, 150), "white").save(image_path)
        store.add_page(project_id, index, str(image_path), 100, 150)
    return store, project_id


def _read_events(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def test_event_line_is_single_json_object_with_ts_and_event() -> None:
    line = event_line("page_started", {"page_index": 3}, now=1700000000.0)
    payload = json.loads(line)
    assert payload["event"] == "page_started"
    assert payload["page_index"] == 3
    assert "ts" in payload
    assert line.count("\n") == 0


def test_event_line_redacts_home_directory() -> None:
    line = event_line("x", {"path": str(Path.home() / "secret")})
    assert str(Path.home()) not in line
    assert "~/secret" in line


def test_event_log_writes_append_lines(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.emit("book_started", pages=2)
    log.emit("book_finished", failed=0)
    log.close()
    events = _read_events(path)
    assert [e["event"] for e in events] == ["book_started", "book_finished"]
    assert events[0]["pages"] == 2


def test_event_log_close_is_idempotent(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "e.jsonl")
    log.emit("a")
    log.close()
    log.close()
    assert len(_read_events(tmp_path / "e.jsonl")) == 1


def test_process_book_emits_book_and_page_events(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 3)
    events_path = tmp_path / "events.jsonl"
    with EventLog(events_path) as log:
        outcome = process_book(
            store,
            project_id,
            primary_engine=FakeEngine("primary", "鸦"),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            max_workers=2,
            event_log=log,
        )
    assert len(outcome.pages) == 3

    events = _read_events(events_path)
    kinds = [e["event"] for e in events]
    assert kinds[0] == "book_started"
    assert kinds[-1] == "book_finished"
    assert kinds.count("page_started") == 3
    assert kinds.count("page_finished") == 3
    assert kinds.count("page_failed") == 0

    started = next(e for e in events if e["event"] == "book_started")
    assert started["pages"] == 3
    assert started["max_workers"] == 2
    assert "gpu_memory_mib" in started

    finished = events[-1]
    assert finished["succeeded"] == 3
    assert finished["failed"] == 0
    assert finished["elapsed_seconds"] >= 0

    for event in events:
        if event["event"] == "page_finished":
            assert "page_index" in event
            assert "elapsed_seconds" in event
            assert event["elapsed_seconds"] >= 0


def test_page_events_carry_page_index_in_order(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 4)
    events_path = tmp_path / "events.jsonl"
    with EventLog(events_path) as log:
        process_book(
            store,
            project_id,
            primary_engine=FakeEngine("primary"),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            max_workers=4,
            event_log=log,
        )
    finished = [e["page_index"] for e in _read_events(events_path) if e["event"] == "page_finished"]
    assert sorted(finished) == [0, 1, 2, 3]


def test_failed_pages_emit_page_failed_and_book_finished_counts(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 3)
    events_path = tmp_path / "events.jsonl"
    with EventLog(events_path) as log:
        outcome = process_book(
            store,
            project_id,
            primary_engine=FailingEngine({1}),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            max_workers=2,
            event_log=log,
        )
    assert len(outcome.failed_pages) == 1

    events = _read_events(events_path)
    failed = [e for e in events if e["event"] == "page_failed"]
    assert len(failed) == 1
    assert failed[0]["page_index"] == 1
    assert "crash on 1" in failed[0]["error"]
    finished = events[-1]
    assert finished["event"] == "book_finished"
    assert finished["succeeded"] == 2
    assert finished["failed"] == 1


def test_cancellation_emits_book_aborted(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 2)
    token = CancellationToken()
    events_path = tmp_path / "events.jsonl"
    with (
        EventLog(events_path) as log,
        cancellation_scope(token),
        pytest.raises(ProcessingCancelled),
    ):
        process_book(
            store,
            project_id,
            primary_engine=CancellingEngine(token),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            max_workers=2,
            event_log=log,
        )
    kinds = [e["event"] for e in _read_events(events_path)]
    assert kinds[0] == "book_started"
    assert "book_aborted" in kinds
    assert "book_finished" not in kinds


def test_no_event_log_means_no_events_and_same_outcome(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 2)
    outcome = process_book(
        store,
        project_id,
        primary_engine=FakeEngine("primary"),
        secondary_engine=FakeEngine("secondary", "雅"),
        crops_dir=tmp_path / "crops",
    )
    assert len(outcome.pages) == 2
    assert not (tmp_path / "events.jsonl").exists()


def test_event_log_is_thread_safe(tmp_path: Path) -> None:
    path = tmp_path / "e.jsonl"
    log = EventLog(path)

    def worker(n: int) -> None:
        for i in range(50):
            log.emit("tick", worker=n, i=i)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.close()
    events = _read_events(path)
    assert len(events) == 400
    assert all(e["event"] == "tick" for e in events)


def test_event_file_paths_are_redacted(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 1)
    events_path = tmp_path / "events.jsonl"
    with EventLog(events_path) as log:
        process_book(
            store,
            project_id,
            primary_engine=FakeEngine("primary"),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            event_log=log,
        )
    raw = events_path.read_text(encoding="utf-8")
    assert str(Path.home()) not in raw


def test_slow_page_event_elapsed_reflects_real_time(tmp_path: Path) -> None:
    class SlowEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            time.sleep(0.05)
            return super().analyze_page(image_path, page_index)

    store, project_id = _store(tmp_path, 1)
    events_path = tmp_path / "events.jsonl"
    with EventLog(events_path) as log:
        process_book(
            store,
            project_id,
            primary_engine=SlowEngine("primary"),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            event_log=log,
        )
    finished = next(e for e in _read_events(events_path) if e["event"] == "page_finished")
    assert finished["elapsed_seconds"] >= 0.05


def test_cli_process_writes_events_file(tmp_path: Path) -> None:
    from scribeforge.cli import Engines, run

    book = tmp_path / "book.png"
    Image.new("RGB", (80, 120), "white").save(book)
    store = tmp_path / "store"
    events = tmp_path / "events.jsonl"

    class BlankEngine:
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            box = BoundingBox(0.1, 0.1, 0.8, 0.02)
            token = OCRToken(self._name, 0.9, box)
            line = OCRLine(self._name, box, (token,))
            return PageOCRResult(page_index, self._name, "1.0", (line,))

    def factory(runtime_root: Path) -> Engines:
        return Engines(primary=BlankEngine("primary"), secondary=BlankEngine("secondary"))

    code = run(
        ["process", str(book), "--store", str(store), "--events-file", str(events)],
        engine_factory=factory,
        stdout=__import__("io").StringIO(),
    )
    assert code == 0
    kinds = [e["event"] for e in _read_events(events)]
    assert kinds[0] == "book_started"
    assert kinds[-1] == "book_finished"
    assert "page_finished" in kinds


def test_process_parser_exposes_events_file() -> None:
    from scribeforge.cli import build_parser

    args = build_parser().parse_args(["process", "book.pdf", "--events-file", "e.jsonl"])
    assert args.events_file == Path("e.jsonl")
    default = build_parser().parse_args(["process", "book.pdf"])
    assert default.events_file is None


def test_summarize_events_aggregates_counts_and_timing() -> None:
    from scribeforge.pipeline.events import summarize_events

    lines = [
        event_line("book_started", {"pages": 3}, now=0),
        event_line("page_started", {"page_index": 0}, now=0),
        event_line("page_finished", {"page_index": 0, "elapsed_seconds": 1.5}, now=1),
        event_line("page_started", {"page_index": 1}, now=1),
        event_line(
            "page_failed", {"page_index": 1, "error": "boom", "elapsed_seconds": 0.5}, now=2
        ),
        event_line("page_finished", {"page_index": 2, "elapsed_seconds": 3.0}, now=5),
        event_line("book_finished", {"succeeded": 2, "failed": 1, "elapsed_seconds": 5.0}, now=5),
    ]
    summary = summarize_events(lines)
    assert summary["events"] == {
        "book_started": 1,
        "page_started": 2,
        "page_finished": 2,
        "page_failed": 1,
        "book_finished": 1,
    }
    assert summary["failed_pages"] == [1]
    assert summary["book_elapsed_seconds"] == 5.0
    assert summary["total_page_seconds"] == 4.5
    assert summary["slowest_page"] == {"page_index": 2, "elapsed_seconds": 3.0}


def test_summarize_events_skips_malformed_and_blank_lines() -> None:
    from scribeforge.pipeline.events import summarize_events

    lines = [
        "",
        "not json at all",
        '{"no_event": true}',
        event_line("book_aborted", {"elapsed_seconds": 2.0}, now=0),
        '{"event": "page_finished", "page_index": "x"}',  # wrong type skipped for timing
    ]
    summary = summarize_events(lines)
    assert summary["events"] == {"book_aborted": 1, "page_finished": 1}
    assert summary["book_elapsed_seconds"] == 2.0
    assert summary["failed_pages"] == []
    assert summary["slowest_page"] is None


def test_summarize_events_empty_input() -> None:
    from scribeforge.pipeline.events import summarize_events

    summary = summarize_events([])
    assert summary["events"] == {}
    assert summary["book_elapsed_seconds"] is None
    assert summary["slowest_page"] is None


def test_serial_path_also_emits_events(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 2)
    events_path = tmp_path / "events.jsonl"
    with EventLog(events_path) as log:
        process_book(
            store,
            project_id,
            primary_engine=FakeEngine("primary"),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            max_workers=1,
            event_log=log,
        )
    kinds = [e["event"] for e in _read_events(events_path)]
    assert kinds.count("page_finished") == 2
    assert kinds[-1] == "book_finished"


def test_book_started_carries_gpu_budget(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 1)
    events_path = tmp_path / "events.jsonl"
    with EventLog(events_path) as log:
        process_book(
            store,
            project_id,
            primary_engine=FakeEngine("primary"),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            max_workers=4,
            gpu_memory_mib=12288,
            event_log=log,
        )
    started = next(e for e in _read_events(events_path) if e["event"] == "book_started")
    assert started["gpu_memory_mib"] == 12288
    assert started["max_workers"] == 2  # 12288 // 6144 default per page


def test_summarize_of_a_real_run_matches_outcome(tmp_path: Path) -> None:
    from scribeforge.pipeline.events import summarize_events

    store, project_id = _store(tmp_path, 3)
    events_path = tmp_path / "events.jsonl"
    with EventLog(events_path) as log:
        outcome = process_book(
            store,
            project_id,
            primary_engine=FailingEngine({2}),
            secondary_engine=FakeEngine("secondary", "雅"),
            crops_dir=tmp_path / "crops",
            max_workers=3,
            event_log=log,
        )
    summary = summarize_events(events_path.read_text(encoding="utf-8").splitlines())
    assert summary["failed_pages"] == [index for index, _ in outcome.failed_pages]
    assert summary["events"]["page_finished"] == len(outcome.pages)
    assert summary["book_elapsed_seconds"] is not None


def test_render_event_summary_markdown() -> None:
    from scribeforge.pipeline.events import summarize_events
    from scribeforge.processing_report import render_event_summary

    lines = [
        event_line("book_started", {"pages": 2}, now=0),
        event_line("page_finished", {"page_index": 0, "elapsed_seconds": 2.0}, now=2),
        event_line("page_failed", {"page_index": 1, "error": "x", "elapsed_seconds": 1.0}, now=3),
        event_line("book_finished", {"succeeded": 1, "failed": 1, "elapsed_seconds": 3.0}, now=3),
    ]
    text = render_event_summary(summarize_events(lines))
    assert "## Event log summary" in text
    assert "failed pages: 1" in text
    assert "slowest page: #0 (2.000s)" in text
    assert "book elapsed: 3.000s" in text


def test_report_command_embeds_event_summary(tmp_path: Path) -> None:
    from scribeforge.cli import Engines, run

    book = tmp_path / "book.png"
    Image.new("RGB", (80, 120), "white").save(book)
    store_root = tmp_path / "store"
    events = tmp_path / "events.jsonl"

    class BlankEngine:
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            box = BoundingBox(0.1, 0.1, 0.8, 0.02)
            token = OCRToken(self._name, 0.9, box)
            line = OCRLine(self._name, box, (token,))
            return PageOCRResult(page_index, self._name, "1.0", (line,))

    def factory(runtime_root: Path) -> Engines:
        return Engines(primary=BlankEngine("primary"), secondary=BlankEngine("secondary"))

    import io

    code = run(
        ["process", str(book), "--store", str(store_root), "--events-file", str(events)],
        engine_factory=factory,
        stdout=io.StringIO(),
    )
    assert code == 0

    report_out = io.StringIO()
    code = run(
        ["report", "--store", str(store_root), "--events", str(events)],
        stdout=report_out,
    )
    assert code == 0
    assert "## Event log summary" in report_out.getvalue()
    assert "book_finished" in report_out.getvalue()

    json_out = io.StringIO()
    code = run(
        ["report", "--store", str(store_root), "--events", str(events), "--json"],
        stdout=json_out,
    )
    assert code == 0
    payload = json.loads(json_out.getvalue())
    assert payload["event_summary"]["events"]["book_finished"] == 1
