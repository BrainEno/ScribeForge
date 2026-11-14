"""Engine-level timing summary for the processing report (issue #69).

The report already lists per-page primary/secondary OCR seconds; what a user
running a multi-hour book actually needs at a glance is the aggregate: how
much wall time each engine burned, which one is the bottleneck, and which
pages were slowest. All values come from persisted latest-attempt timestamp
spans only — nothing is inferred, and unmeasured work stays ``None``.
"""

from __future__ import annotations

import json
from pathlib import Path

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.processing_report import (
    build_processing_report,
    processing_report_dict,
    processing_report_json,
    render_processing_report,
)
from tests.test_processing_report import _finish_job, _verification


def _timed_project(tmp_path: Path):
    """Three pages with distinct primary/secondary OCR timings."""

    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "/books/book.pdf", "hash")
    for page in range(3):
        store.add_page(project_id, page, f"pages/{page}.png", 100, 200)
    database = tmp_path / "project.sqlite3"
    # page 0: primary 10s, secondary 2s; page 1: primary 4s, secondary 8s;
    # page 2: primary 6s, secondary unmeasured (still running).
    plan = [
        (0, JobStage.PRIMARY_OCR, JobState.SUCCEEDED, 10),
        (0, JobStage.SECONDARY_OCR, JobState.SUCCEEDED, 2),
        (1, JobStage.PRIMARY_OCR, JobState.SUCCEEDED, 4),
        (1, JobStage.SECONDARY_OCR, JobState.SUCCEEDED, 8),
        (2, JobStage.PRIMARY_OCR, JobState.SUCCEEDED, 6),
    ]
    for page, stage, state, seconds in plan:
        _finish_job(
            store,
            database,
            project_id,
            stage,
            page_index=page,
            state=state,
            started_at="2026-01-01 00:00:00",
            finished_at=f"2026-01-01 00:00:{seconds:02d}",
        )
    job_id = store.ensure_job(project_id, JobStage.SECONDARY_OCR, page_index=2)
    store.start_job(job_id)  # running: no finished_at -> unmeasured
    for page in range(3):
        _verification(store, project_id, page)
    return store, project_id


def test_engine_timings_aggregate_per_engine_totals(tmp_path: Path) -> None:
    store, project_id = _timed_project(tmp_path)

    report = build_processing_report(store, project_id)
    engines = {item.engine: item for item in report.engine_timings}

    assert set(engines) == {"primary", "secondary"}
    primary, secondary = engines["primary"], engines["secondary"]
    assert primary.stage == "primary_ocr"
    assert secondary.stage == "secondary_ocr"
    assert primary.timed_jobs == 3
    assert primary.observed_seconds == 20.0
    assert secondary.timed_jobs == 2
    assert secondary.observed_seconds == 10.0
    assert secondary.unavailable_jobs == 1
    # Shares are over all measured job time (30 s): primary 2/3, secondary 1/3.
    assert primary.share == 20.0 / 30.0
    assert secondary.share == 10.0 / 30.0
    assert primary.mean_seconds == 20.0 / 3
    assert secondary.mean_seconds == 10.0 / 2


def test_engine_timings_are_none_not_zero_when_nothing_is_measured(tmp_path: Path) -> None:
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "empty.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "/books/book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    store.start_job(job_id)  # running, never finished

    report = build_processing_report(store, project_id)
    engines = {item.engine: item for item in report.engine_timings}

    assert engines["primary"].observed_seconds is None
    assert engines["primary"].share is None
    assert engines["primary"].mean_seconds is None
    assert engines["primary"].timed_jobs == 0
    assert engines["primary"].unavailable_jobs == 1
    assert report.bottleneck_stage is None


def test_bottleneck_stage_is_the_slowest_measured_stage(tmp_path: Path) -> None:
    store, project_id = _timed_project(tmp_path)

    report = build_processing_report(store, project_id)

    assert report.bottleneck_stage == "primary_ocr"


def test_markdown_report_has_engine_timing_and_slowest_pages_sections(tmp_path: Path) -> None:
    store, project_id = _timed_project(tmp_path)

    text = render_processing_report(build_processing_report(store, project_id))

    assert "## Engine timing" in text
    assert "| Engine | Stage | Timed jobs | Unavailable | Observed s | Share | Mean s/job |" in text
    assert "| primary | primary_ocr | 3 | 0 | 20.000 | 66.67% | 6.667 |" in text
    assert "| secondary | secondary_ocr | 2 | 1 | 10.000 | 33.33% | 5.000 |" in text
    assert "bottleneck stage: `primary_ocr`" in text
    assert "## Slowest pages" in text
    # page 0 (12 s) and page 1 (12 s) tie, broken by page index; page 2 (6 s) last
    slowest = text.split("## Slowest pages", 1)[1].split("## ", 1)[0]
    assert slowest.index("| 1 |") < slowest.index("| 2 |") < slowest.index("| 3 |")


def test_json_report_carries_engine_timings_and_bottleneck(tmp_path: Path) -> None:
    store, project_id = _timed_project(tmp_path)

    payload = json.loads(processing_report_json(build_processing_report(store, project_id)))
    timing = payload["timing"]

    assert timing["bottleneck_stage"] == "primary_ocr"
    engines = {item["engine"]: item for item in timing["engines"]}
    assert engines["primary"]["observed_seconds"] == 20.0
    assert engines["primary"]["share"] == 20.0 / 30.0
    assert engines["secondary"]["unavailable_jobs"] == 1
    assert payload["slowest_pages"][0]["page_index"] == 0
    assert payload["slowest_pages"][0]["observed_seconds"] == 12.0


def test_json_report_engine_fields_are_null_when_unmeasured(tmp_path: Path) -> None:
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "empty.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "/books/book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)

    payload = json.loads(processing_report_json(build_processing_report(store, project_id)))

    assert payload["timing"]["bottleneck_stage"] is None
    assert payload["timing"]["engines"][0]["observed_seconds"] is None
    assert payload["timing"]["engines"][0]["share"] is None
    assert payload["slowest_pages"] == []


def test_report_dict_is_deterministic_with_engine_timings(tmp_path: Path) -> None:
    store, project_id = _timed_project(tmp_path)

    first = processing_report_dict(build_processing_report(store, project_id))
    second = processing_report_dict(build_processing_report(store, project_id))

    assert first == second
    assert "engines" in first["timing"]


# ---------------------------------------------------------------------------
# configurable slowest-pages limit (report --top N)
# ---------------------------------------------------------------------------


def test_slowest_pages_limit_is_configurable(tmp_path: Path) -> None:
    store, project_id = _timed_project(tmp_path)
    report = build_processing_report(store, project_id)

    full = render_processing_report(report)
    limited = render_processing_report(report, slowest_limit=2)
    one = render_processing_report(report, slowest_limit=1)

    def rows(text: str) -> list[str]:
        section = text.split("## Slowest pages", 1)[1].split("## ", 1)[0]
        return [line for line in section.splitlines() if line.startswith("| ")][2:]

    assert len(rows(full)) == 3
    assert len(rows(limited)) == 2
    assert len(rows(one)) == 1
    assert rows(one)[0].startswith("| 1 |")


def test_report_dict_and_json_accept_slowest_limit(tmp_path: Path) -> None:
    store, project_id = _timed_project(tmp_path)
    report = build_processing_report(store, project_id)

    assert len(processing_report_dict(report, slowest_limit=2)["slowest_pages"]) == 2
    payload = json.loads(
        processing_report_json(build_processing_report(store, project_id), slowest_limit=1)
    )
    assert len(payload["slowest_pages"]) == 1
    assert payload["slowest_pages"][0]["page_index"] == 0


def test_cli_report_top_flag_reaches_json_output(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    _timed_project(tmp_path)
    stdout = io.StringIO()

    code = run(
        [
            "report",
            "--store",
            str(tmp_path),
            "--json",
            "--top",
            "1",
        ],
        stdout=stdout,
    )

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert len(payload["slowest_pages"]) == 1


# ---------------------------------------------------------------------------
# report --compare: engine timing deltas
# ---------------------------------------------------------------------------


def _timing_payload(primary_seconds: float | None, secondary_seconds: float | None) -> dict:
    return {
        "schema_version": 1,
        "timing": {
            "observed_seconds": None,
            "engines": [
                {"engine": "primary", "observed_seconds": primary_seconds},
                {"engine": "secondary", "observed_seconds": secondary_seconds},
            ],
        },
    }


def test_compare_reports_engine_seconds_deltas() -> None:
    from scribeforge.processing_report import compare_report_dicts

    comparison = compare_report_dicts(_timing_payload(10.0, 4.0), _timing_payload(25.0, None))

    assert comparison.engine_seconds_delta == {"primary": 15.0, "secondary": None}


def test_comparison_markdown_lists_engine_deltas() -> None:
    from scribeforge.processing_report import compare_report_dicts, render_comparison_markdown

    text = render_comparison_markdown(
        compare_report_dicts(_timing_payload(10.0, 4.0), _timing_payload(25.0, 9.0))
    )

    assert "## Engine timing delta" in text
    assert "| primary | +15 |" in text
    assert "| secondary | +5 |" in text


# ---------------------------------------------------------------------------
# status command: timing summary line
# ---------------------------------------------------------------------------


def test_status_json_includes_observed_seconds_and_bottleneck(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    _timed_project(tmp_path)
    stdout = io.StringIO()

    code = run(["status", "--store", str(tmp_path), "--json"], stdout=stdout)

    assert code == 0
    payload = json.loads(stdout.getvalue())
    project = payload["projects"][0]
    assert project["observed_seconds"] == 30.0
    assert project["bottleneck_stage"] == "primary_ocr"
    assert project["engine_seconds"]["primary"] == 20.0
    assert project["engine_seconds"]["secondary"] == 10.0


def test_status_markdown_shows_bottleneck_line(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    _timed_project(tmp_path)
    stdout = io.StringIO()

    code = run(["status", "--store", str(tmp_path)], stdout=stdout)

    assert code == 0
    text = stdout.getvalue()
    assert "observed job time: 30.000 s" in text
    assert "bottleneck stage: primary_ocr" in text


# ---------------------------------------------------------------------------
# CLI end-to-end: report markdown sections, --compare, edge rendering
# ---------------------------------------------------------------------------


def test_cli_report_markdown_contains_engine_and_slowest_sections(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run
    from scribeforge.storage.sqlite import SQLiteStore

    _timed_project(tmp_path)
    store = SQLiteStore(tmp_path / "project.sqlite3")
    project_id = store.list_projects()[0].id
    stdout = io.StringIO()

    code = run(
        ["report", "--store", str(tmp_path), "--project", str(project_id), "--top", "2"],
        stdout=stdout,
    )

    assert code == 0
    text = stdout.getvalue()
    assert "## Engine timing" in text
    assert "| primary | primary_ocr | 3 | 0 | 20.000 | 66.67% | 6.667 |" in text
    assert "bottleneck stage: `primary_ocr`" in text
    section = text.split("## Slowest pages", 1)[1].split("## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")][2:]
    assert len(rows) == 2  # --top 2 respected


def test_cli_report_compare_renders_engine_deltas(tmp_path: Path) -> None:
    import io
    import sqlite3

    from scribeforge.cli import run

    _store, project_id = _timed_project(tmp_path)
    first = io.StringIO()
    assert (
        run(
            ["report", "--store", str(tmp_path), "--project", str(project_id), "--json"],
            stdout=first,
        )
        == 0
    )
    old_path = tmp_path / "old.json"
    old_path.write_text(first.getvalue(), encoding="utf-8")

    # Make the primary engine slower, then compare against the old export.
    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute(
            "UPDATE jobs SET finished_at='2026-01-01 00:00:30' "
            "WHERE stage='primary_ocr' AND page_id="
            "(SELECT id FROM pages WHERE page_index=0)"
        )
    second = io.StringIO()
    code = run(
        [
            "report",
            "--store",
            str(tmp_path),
            "--project",
            str(project_id),
            "--compare",
            str(old_path),
        ],
        stdout=second,
    )

    assert code == 0
    text = second.getvalue()
    assert "## Engine timing delta" in text
    assert "| primary | +20 |" in text


def test_markdown_report_renders_dash_for_unmeasured_engine(tmp_path: Path) -> None:
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "empty.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "/books/book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)

    text = render_processing_report(build_processing_report(store, project_id))

    assert "| primary | primary_ocr | 0 | 0 | — | — | — |" in text
    assert "bottleneck stage: not measurable" in text
    assert "No page has a measured duration yet." in text


def test_markdown_report_is_deterministic(tmp_path: Path) -> None:
    store, project_id = _timed_project(tmp_path)
    first = render_processing_report(build_processing_report(store, project_id))
    second = render_processing_report(build_processing_report(store, project_id))
    assert first == second


# ---------------------------------------------------------------------------
# edge cases and backwards compatibility
# ---------------------------------------------------------------------------


def test_compare_tolerates_legacy_payloads_without_engines() -> None:
    """Old schema-v1 exports predate timing.engines; they must not crash compare."""
    from scribeforge.processing_report import compare_report_dicts

    legacy = {"schema_version": 1, "timing": {"observed_seconds": 5.0}}
    comparison = compare_report_dicts(legacy, _timing_payload(10.0, 4.0))

    assert comparison.engine_seconds_delta == {"primary": None, "secondary": None}
    assert comparison.observed_seconds_delta is None  # old side measured, new side None


def test_slowest_pages_rejects_non_positive_limit(tmp_path: Path) -> None:
    import pytest

    store, project_id = _timed_project(tmp_path)
    report = build_processing_report(store, project_id)

    with pytest.raises(ValueError, match="limit must be >= 1"):
        report.slowest_pages(0)


def test_cli_report_top_zero_is_rejected(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    _timed_project(tmp_path)
    code = run(
        ["report", "--store", str(tmp_path), "--json", "--top", "0"],
        stdout=io.StringIO(),
    )
    assert code == 2  # EXIT_FATAL


def test_engine_timing_when_only_one_engine_is_measured(tmp_path: Path) -> None:

    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "one.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "/books/book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    database = tmp_path / "one.sqlite3"
    _finish_job(
        store,
        database,
        project_id,
        JobStage.SECONDARY_OCR,
        page_index=0,
        state=JobState.SUCCEEDED,
        started_at="2026-01-01 00:00:00",
        finished_at="2026-01-01 00:00:07",
    )

    report = build_processing_report(store, project_id)
    engines = {item.engine: item for item in report.engine_timings}

    assert engines["secondary"].observed_seconds == 7.0
    assert engines["secondary"].share == 1.0  # the only measured time
    assert engines["primary"].observed_seconds is None
    assert engines["primary"].share is None
    assert report.bottleneck_stage == "secondary_ocr"


def test_status_markdown_handles_unmeasured_project(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    store.create_project("Book", "/books/book.pdf", "hash")
    stdout = io.StringIO()

    code = run(["status", "--store", str(tmp_path)], stdout=stdout)

    assert code == 0
    text = stdout.getvalue()
    assert "observed job time: unknown" in text
    assert "bottleneck stage: not measurable" in text
    assert "engine primary: unknown" in text


def test_bottleneck_tie_breaks_to_earliest_pipeline_stage(tmp_path: Path) -> None:
    """Equal measured times pick the earliest stage in pipeline order."""

    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "/books/book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    database = tmp_path / "project.sqlite3"
    for stage in (JobStage.PREPROCESS, JobStage.PRIMARY_OCR):
        _finish_job(
            store,
            database,
            project_id,
            stage,
            page_index=0,
            state=JobState.SUCCEEDED,
            started_at="2026-01-01 00:00:00",
            finished_at="2026-01-01 00:00:05",
        )

    report = build_processing_report(store, project_id)
    assert report.bottleneck_stage == "preprocess"


def test_status_json_schema_v1_keeps_existing_keys(tmp_path: Path) -> None:
    """New timing keys are additive; existing status consumers keep working."""
    import io

    from scribeforge.cli import run

    _timed_project(tmp_path)
    stdout = io.StringIO()

    code = run(["status", "--store", str(tmp_path), "--json"], stdout=stdout)

    assert code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == 1
    project = payload["projects"][0]
    for key in (
        "id",
        "title",
        "source_uri",
        "pages_total",
        "pages_with_both_ocr_success",
        "review_queued",
        "job_counts",
        "active_jobs",
        "failed_jobs",
    ):
        assert key in project


def test_engine_timing_section_present_even_without_any_jobs(tmp_path: Path) -> None:
    """The section always renders so an empty project is visibly unmeasured."""
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "/books/book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)

    text = render_processing_report(build_processing_report(store, project_id))

    assert "## Engine timing" in text
    assert "| secondary | secondary_ocr | 0 | 0 | — | — | — |" in text
