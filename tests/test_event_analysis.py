"""Event-log analysis tests (issue #69).

`scribeforge events <file.jsonl>` turns a raw pipeline event trace into a
bottleneck report: per-stage totals and means (which engine is slower), the
slowest pages, failure-reason counts, and book-level elapsed time. The parser
skips malformed lines so a crash mid-write never breaks the analysis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribeforge.pipeline.event_analysis import (
    analyze_events,
    events_json,
    parse_events,
    render_events_markdown,
)
from scribeforge.pipeline.events import event_line


def _lines() -> list[str]:
    return [
        event_line("book_started", {"project_id": 1, "pages": 3, "max_workers": 2}, now=100.0),
        event_line("page_started", {"page_index": 0}, now=100.1),
        event_line(
            "stage_finished",
            {"stage": "primary_ocr", "page_index": 0, "elapsed_seconds": 2.0},
            now=102.1,
        ),
        event_line(
            "stage_finished",
            {"stage": "secondary_ocr", "page_index": 0, "elapsed_seconds": 4.0},
            now=106.1,
        ),
        event_line("page_finished", {"page_index": 0, "elapsed_seconds": 6.0}, now=106.2),
        event_line("page_started", {"page_index": 1}, now=106.3),
        event_line(
            "stage_finished",
            {"stage": "primary_ocr", "page_index": 1, "elapsed_seconds": 3.0},
            now=109.3,
        ),
        event_line(
            "stage_finished",
            {"stage": "secondary_ocr", "page_index": 1, "elapsed_seconds": 5.0},
            now=114.3,
        ),
        event_line("page_finished", {"page_index": 1, "elapsed_seconds": 8.0}, now=114.4),
        event_line("page_started", {"page_index": 2}, now=114.5),
        event_line(
            "page_failed",
            {"page_index": 2, "error": "engine crashed", "elapsed_seconds": 1.5},
            now=116.0,
        ),
        event_line(
            "book_finished",
            {"project_id": 1, "succeeded": 2, "failed": 1, "elapsed_seconds": 16.0},
            now=116.1,
        ),
    ]


def test_parse_events_skips_malformed_lines() -> None:
    lines = _lines() + ["not json", "", '{"event": 42}', '{"no_event": true}']
    events = parse_events(lines)
    assert len(events) == len(_lines())
    assert events[0].event == "book_started"
    assert events[0].fields["pages"] == 3


def test_parse_events_accepts_file_objects() -> None:
    events = parse_events(iter(_lines()))
    assert len(events) == len(_lines())


def test_analyze_per_stage_totals_and_means() -> None:
    analysis = analyze_events(parse_events(_lines()))
    stages = {s.stage: s for s in analysis.stages}
    assert set(stages) == {"primary_ocr", "secondary_ocr"}
    assert stages["primary_ocr"].count == 2
    assert stages["primary_ocr"].total_seconds == pytest.approx(5.0)
    assert stages["primary_ocr"].mean_seconds == pytest.approx(2.5)
    assert stages["secondary_ocr"].total_seconds == pytest.approx(9.0)
    # the "which engine is slower" comparison
    assert analysis.slowest_stage == "secondary_ocr"


def test_analyze_page_durations_and_slowest() -> None:
    analysis = analyze_events(parse_events(_lines()))
    durations = {p.page_index: p.elapsed_seconds for p in analysis.pages}
    assert durations == {0: 6.0, 1: 8.0, 2: 1.5}
    assert analysis.slowest_pages(2) == [(1, 8.0), (0, 6.0)]
    assert analysis.slowest_pages(10)[2] == (2, 1.5)


def test_analyze_failures_and_book_summary() -> None:
    analysis = analyze_events(parse_events(_lines()))
    assert analysis.failed_pages == [2]
    assert analysis.failure_reasons == {"engine crashed": 1}
    assert analysis.book_elapsed_seconds == pytest.approx(16.0)
    assert analysis.event_counts["page_finished"] == 2
    assert analysis.succeeded == 2
    assert analysis.failed == 1


def test_analyze_empty_trace() -> None:
    analysis = analyze_events([])
    assert analysis.stages == ()
    assert analysis.pages == ()
    assert analysis.slowest_stage is None
    assert analysis.book_elapsed_seconds is None
    assert analysis.failed_pages == []


def test_analyze_aborted_book_marks_incomplete() -> None:
    lines = _lines()[:-1] + [
        event_line(
            "book_aborted",
            {"project_id": 1, "succeeded": 2, "failed": 0, "elapsed_seconds": 9.5},
            now=120.0,
        )
    ]
    analysis = analyze_events(parse_events(lines))
    assert analysis.aborted is True
    assert analysis.book_elapsed_seconds == pytest.approx(9.5)


def test_render_markdown_contains_sections() -> None:
    text = render_events_markdown(analyze_events(parse_events(_lines())))
    assert "# Event log analysis" in text
    assert "## Stage timings" in text
    assert "secondary_ocr" in text
    assert "## Slowest pages" in text
    assert "## Failures" in text
    assert "engine crashed" in text
    assert "16.0" in text


def test_events_json_is_parseable_and_stable() -> None:
    analysis = analyze_events(parse_events(_lines()))
    payload = json.loads(events_json(analysis))
    assert payload["book_elapsed_seconds"] == pytest.approx(16.0)
    assert payload["failed_pages"] == [2]
    assert payload["slowest_stage"] == "secondary_ocr"
    assert payload["stages"][0]["stage"] in ("primary_ocr", "secondary_ocr")


def test_unpaired_page_started_is_reported_as_in_flight() -> None:
    lines = _lines()[:-2]  # drop book_finished and the last page_failed
    analysis = analyze_events(parse_events(lines))
    assert 2 in analysis.in_flight_pages
    assert analysis.aborted is False  # no book_aborted either
    assert analysis.book_elapsed_seconds is None


def test_process_emits_stage_finished_events(tmp_path: Path) -> None:
    import io

    # Build a one-page book through the same helpers the CLI tests use.
    from PIL import Image

    from scribeforge.cli import run

    book = tmp_path / "book.png"
    Image.new("RGB", (80, 120), "white").save(book)
    store_dir = tmp_path / "store"
    events_path = tmp_path / "events.jsonl"

    from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult

    class BlankEngine:
        def __init__(self, name: str) -> None:
            self._name = name

        @property
        def name(self) -> str:
            return self._name

        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            box = BoundingBox(0.1, 0.1, 0.8, 0.02)
            token = OCRToken(self._name, 0.9, box)
            return PageOCRResult(
                page_index, self._name, "1.0", (OCRLine(self._name, box, (token,)),)
            )

    from scribeforge.cli import Engines

    def factory(runtime_root):
        return Engines(primary=BlankEngine("primary"), secondary=BlankEngine("secondary"))

    code = run(
        ["process", str(book), "--store", str(store_dir), "--events-file", str(events_path)],
        engine_factory=factory,
        stdout=io.StringIO(),
    )
    assert code == 0
    events = parse_events(events_path.read_text(encoding="utf-8").splitlines())
    stage_events = [e for e in events if e.event == "stage_finished"]
    stages = {e.fields["stage"] for e in stage_events}
    assert "primary_ocr" in stages
    assert "secondary_ocr" in stages
    assert all(e.fields["page_index"] == 0 for e in stage_events)
    assert all(e.fields["elapsed_seconds"] >= 0 for e in stage_events)

    analysis = analyze_events(events)
    assert analysis.slowest_stage in ("primary_ocr", "secondary_ocr", "verify")
    assert analysis.book_elapsed_seconds is not None
    assert analysis.failed_pages == []


def test_cli_events_command_markdown_and_json(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    log = tmp_path / "events.jsonl"
    log.write_text("\n".join(_lines()) + "\n", encoding="utf-8")

    out = io.StringIO()
    code = run(["events", str(log)], stdout=out)
    assert code == 0
    text = out.getvalue()
    assert "# Event log analysis" in text
    assert "secondary_ocr" in text

    out_json = io.StringIO()
    code = run(["events", str(log), "--json", "--top", "1"], stdout=out_json)
    assert code == 0
    payload = json.loads(out_json.getvalue())
    assert payload["slowest_pages"] == [{"page_index": 1, "elapsed_seconds": 8.0}]
    assert payload["slowest_stage"] == "secondary_ocr"


def test_cli_events_missing_file_is_fatal(tmp_path: Path, capsys) -> None:
    import io

    from scribeforge.cli import run

    code = run(["events", str(tmp_path / "nope.jsonl")], stdout=io.StringIO())
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_cli_events_rejects_bad_top(tmp_path: Path, capsys) -> None:
    import io

    from scribeforge.cli import run

    log = tmp_path / "events.jsonl"
    log.write_text("\n".join(_lines()) + "\n", encoding="utf-8")
    code = run(["events", str(log), "--top", "0"], stdout=io.StringIO())
    assert code == 2
    assert "--top" in capsys.readouterr().err


def test_event_log_thread_safety_under_parallel_pages(tmp_path: Path) -> None:
    import threading

    from scribeforge.pipeline.events import EventLog

    log = EventLog(tmp_path / "parallel.jsonl")
    threads = [
        threading.Thread(
            target=lambda i=i: log.emit("page_finished", page_index=i, elapsed_seconds=1.0)
        )
        for i in range(50)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.close()
    events = parse_events((tmp_path / "parallel.jsonl").read_text(encoding="utf-8").splitlines())
    assert len(events) == 50
    assert len(analyze_events(events).pages) == 50


def test_analyze_handles_duplicate_and_out_of_order_pages() -> None:
    lines = [
        event_line("page_finished", {"page_index": 5, "elapsed_seconds": 3.0}, now=1.0),
        event_line("page_finished", {"page_index": 2, "elapsed_seconds": 9.0}, now=2.0),
        event_line("page_finished", {"page_index": 5, "elapsed_seconds": 1.0}, now=3.0),
    ]
    analysis = analyze_events(parse_events(lines))
    durations = {p.page_index: p.elapsed_seconds for p in analysis.pages}
    assert durations == {2: 9.0, 5: 1.0}  # later event wins, pages sorted by index
    assert analysis.slowest_pages(1) == [(2, 9.0)]


def test_stage_event_without_elapsed_is_ignored() -> None:
    lines = [
        event_line("stage_finished", {"stage": "verify", "page_index": 0}, now=1.0),
        event_line(
            "stage_finished", {"stage": "verify", "page_index": 0, "elapsed_seconds": "x"}, now=2.0
        ),
        event_line(
            "stage_finished", {"stage": "verify", "page_index": 0, "elapsed_seconds": 2.5}, now=3.0
        ),
    ]
    analysis = analyze_events(parse_events(lines))
    assert len(analysis.stages) == 1
    assert analysis.stages[0].count == 1
    assert analysis.stages[0].total_seconds == pytest.approx(2.5)


def test_book_events_without_elapsed_leave_none() -> None:
    lines = [event_line("book_finished", {"succeeded": 1, "failed": 0}, now=1.0)]
    analysis = analyze_events(parse_events(lines))
    assert analysis.book_elapsed_seconds is None
    assert analysis.succeeded == 1
    assert analysis.aborted is False


def test_render_empty_analysis() -> None:
    text = render_events_markdown(analyze_events([]))
    assert "# Event log analysis" in text
    assert "## Stage timings" in text
    assert "Bottleneck" not in text


def test_events_json_top_limits_slowest_pages() -> None:
    analysis = analyze_events(parse_events(_lines()))
    payload = json.loads(events_json(analysis, top=1))
    assert payload["slowest_pages"] == [{"page_index": 1, "elapsed_seconds": 8.0}]


def test_failure_reason_falls_back_to_unknown() -> None:
    lines = [event_line("page_failed", {"page_index": 0, "error": "   "}, now=1.0)]
    analysis = analyze_events(parse_events(lines))
    assert analysis.failure_reasons == {"unknown": 1}


def test_multiple_books_in_one_trace_last_book_wins() -> None:
    lines = [
        event_line("book_started", {"project_id": 1, "pages": 1}, now=1.0),
        event_line(
            "book_finished",
            {"project_id": 1, "succeeded": 1, "failed": 0, "elapsed_seconds": 5.0},
            now=6.0,
        ),
        event_line("book_started", {"project_id": 2, "pages": 2}, now=10.0),
        event_line(
            "book_finished",
            {"project_id": 2, "succeeded": 2, "failed": 0, "elapsed_seconds": 12.0},
            now=22.0,
        ),
    ]
    analysis = analyze_events(parse_events(lines))
    assert analysis.book_elapsed_seconds == pytest.approx(12.0)
    assert analysis.succeeded == 2


def test_cli_events_parser_defaults() -> None:
    from scribeforge.cli import build_parser

    args = build_parser().parse_args(["events", "run.jsonl"])
    assert args.json is False
    assert args.top == 10
    with pytest.raises(SystemExit):
        build_parser().parse_args(["events"])


def test_in_flight_pages_empty_when_all_finished() -> None:
    analysis = analyze_events(parse_events(_lines()))
    assert analysis.in_flight_pages == ()


def test_event_fields_exclude_event_key() -> None:
    (event,) = parse_events([event_line("x", {"a": 1}, now=1.0)])
    assert event.event == "x"
    assert event.fields == {"a": 1, "ts": 1.0}


def test_engine_comparison_present_when_both_stages_exist() -> None:
    analysis = analyze_events(parse_events(_lines()))
    comparison = analysis.engine_comparison()
    assert comparison is not None
    primary, secondary = comparison
    assert primary.stage == "primary_ocr" and secondary.stage == "secondary_ocr"
    text = render_events_markdown(analysis)
    assert "## Engine comparison" in text
    assert "**Slower engine: secondary**" in text
    assert "pages/min" in text


def test_engine_comparison_absent_without_both_engines() -> None:
    lines = [
        event_line("page_finished", {"page_index": 0, "elapsed_seconds": 55.0}, now=56.0),
        event_line(
            "stage_finished",
            {"stage": "primary_ocr", "page_index": 0, "elapsed_seconds": 1.0},
            now=1.0,
        ),
        event_line(
            "book_finished", {"succeeded": 1, "failed": 0, "elapsed_seconds": 60.0}, now=61.0
        ),
    ]
    analysis = analyze_events(parse_events(lines))
    assert analysis.engine_comparison() is None
    text = render_events_markdown(analysis)
    assert "## Engine comparison" not in text
    payload = json.loads(events_json(analysis))
    assert payload["engine_comparison"] is None
    assert payload["pages_per_minute"] == pytest.approx(1.0)


def test_engine_comparison_in_json() -> None:
    analysis = analyze_events(parse_events(_lines()))
    payload = json.loads(events_json(analysis))
    comparison = payload["engine_comparison"]
    assert comparison["primary"]["count"] == 2
    assert comparison["secondary"]["total_seconds"] == pytest.approx(9.0)
    assert payload["total_page_seconds"] == pytest.approx(15.5)


def test_pages_per_minute_none_without_elapsed() -> None:
    analysis = analyze_events(
        parse_events(
            [event_line("page_finished", {"page_index": 0, "elapsed_seconds": 2.0}, now=1.0)]
        )
    )
    assert analysis.pages_per_minute is None


def test_analyze_captures_book_started_workers_and_pages() -> None:
    analysis = analyze_events(parse_events(_lines()))
    assert analysis.book_pages == 3
    assert analysis.book_workers == 2


def test_estimate_remaining_from_trace() -> None:
    from scribeforge.domain.scheduling import estimate_remaining_seconds

    analysis = analyze_events(parse_events(_lines()))
    observed = [p.elapsed_seconds for p in analysis.pages]
    # mean 5.1666..., 4 remaining pages, 2 workers
    estimated = estimate_remaining_seconds(
        observed, remaining_pages=4, workers=analysis.book_workers or 1
    )
    assert estimated == pytest.approx(4 * (6.0 + 8.0 + 1.5) / 3 / 2)


def test_cli_events_estimate_remaining_and_plan(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    log = tmp_path / "events.jsonl"
    log.write_text("\n".join(_lines()) + "\n", encoding="utf-8")
    out = io.StringIO()
    code = run(
        ["events", str(log), "--estimate-remaining", "4", "--plan"],
        stdout=out,
    )
    assert code == 0
    text = out.getvalue()
    assert "estimated remaining:" in text
    assert "4 page(s) at 2 worker(s)" in text
    assert "## Simulated schedule" in text
    assert "waves: 2" in text  # 3 declared pages at 2 workers


def test_cli_events_estimate_rejects_negative(tmp_path: Path, capsys) -> None:
    import io

    from scribeforge.cli import run

    log = tmp_path / "events.jsonl"
    log.write_text("\n".join(_lines()) + "\n", encoding="utf-8")
    code = run(["events", str(log), "--estimate-remaining", "-1"], stdout=io.StringIO())
    assert code == 2
    assert "--estimate-remaining" in capsys.readouterr().err


def test_cli_events_estimate_without_book_started_uses_one_worker(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    log = tmp_path / "events.jsonl"
    lines = [
        event_line("page_finished", {"page_index": 0, "elapsed_seconds": 2.0}, now=1.0),
        event_line("page_finished", {"page_index": 1, "elapsed_seconds": 4.0}, now=2.0),
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = io.StringIO()
    code = run(["events", str(log), "--estimate-remaining", "3"], stdout=out)
    assert code == 0
    assert "at 1 worker(s)" in out.getvalue()
    # mean 3s x 3 pages / 1 worker = 9s
    assert "9.0 s" in out.getvalue()


def test_cli_events_plan_skipped_without_declared_pages(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    log = tmp_path / "events.jsonl"
    lines = [event_line("page_finished", {"page_index": 0, "elapsed_seconds": 2.0}, now=1.0)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = io.StringIO()
    code = run(
        ["events", str(log), "--estimate-remaining", "2", "--plan"],
        stdout=out,
    )
    assert code == 0
    text = out.getvalue()
    assert "estimated remaining:" in text
    assert "plan:" not in text  # no book_started -> no declared page count


def test_events_json_includes_book_workers_and_pages() -> None:
    analysis = analyze_events(parse_events(_lines()))
    payload = json.loads(events_json(analysis))
    assert payload["book_pages"] == 3
    assert payload["book_workers"] == 2


def test_cli_events_plan_renders_markdown_table(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    log = tmp_path / "events.jsonl"
    log.write_text("\n".join(_lines()) + "\n", encoding="utf-8")
    out = io.StringIO()
    code = run(["events", str(log), "--estimate-remaining", "2", "--plan"], stdout=out)
    assert code == 0
    text = out.getvalue()
    assert "## Simulated schedule" in text
    assert "| 1 | 2 |" in text  # 3 declared pages at 2 workers -> waves 2+1


def test_events_json_estimate_block() -> None:
    analysis = analyze_events(parse_events(_lines()))
    payload = json.loads(events_json(analysis, estimate_remaining_pages=4))
    assert payload["estimate"]["remaining_pages"] == 4
    assert payload["estimate"]["workers"] == 2
    assert payload["estimate"]["observed_pages"] == 3
    # mean (6+8+1.5)/3 = 5.1667; 4 pages / 2 workers -> 10.3333
    assert payload["estimate"]["estimated_remaining_seconds"] == pytest.approx(10.333333, abs=1e-4)


def test_events_json_without_estimate_has_no_block() -> None:
    analysis = analyze_events(parse_events(_lines()))
    payload = json.loads(events_json(analysis))
    assert "estimate" not in payload


def test_cli_events_json_estimate(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    log = tmp_path / "events.jsonl"
    log.write_text("\n".join(_lines()) + "\n", encoding="utf-8")
    out = io.StringIO()
    code = run(
        ["events", str(log), "--json", "--estimate-remaining", "4"],
        stdout=out,
    )
    assert code == 0
    payload = json.loads(out.getvalue())
    assert payload["estimate"]["estimated_remaining_seconds"] == pytest.approx(10.333333, abs=1e-4)
