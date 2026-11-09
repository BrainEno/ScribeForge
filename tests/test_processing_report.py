from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scribeforge.cli import run
from scribeforge.domain.alignment import LineAlignment, PageAlignment
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.risk import PairRisk, RiskReason
from scribeforge.processing_report import (
    build_processing_report,
    processing_report_dict,
    render_processing_report,
)
from scribeforge.storage.sqlite import SQLiteStore


def _finish_job(
    store: SQLiteStore,
    database: Path,
    project_id: int,
    stage: JobStage,
    *,
    page_index: int | None,
    state: JobState,
    started_at: str,
    finished_at: str,
) -> None:
    job_id = store.ensure_job(project_id, stage, page_index=page_index)
    store.start_job(job_id)
    if state is JobState.SUCCEEDED:
        store.complete_job(job_id)
    else:
        store.fail_job(job_id, "private failure with /home/alice/book.pdf")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE jobs SET started_at=?, finished_at=? WHERE id=?",
            (started_at, finished_at, job_id),
        )


def _verification(store: SQLiteStore, project_id: int, page_index: int) -> int:
    lines = tuple(OCRLine(text, BoundingBox(0.1, 0.1, 0.8, 0.1)) for text in ("甲", "乙"))
    primary = store.record_ocr(project_id, PageOCRResult(page_index, "primary", "1", lines))
    secondary = store.record_ocr(project_id, PageOCRResult(page_index, "secondary", "1", lines))
    alignment = PageAlignment(
        page_index,
        "primary",
        "secondary",
        (
            LineAlignment(0, 0, 1.0, 0.0, ()),
            LineAlignment(1, 1, 1.0, 0.0, ()),
        ),
    )
    risks = (
        PairRisk(0, 0.7, (RiskReason.TEXT_DISAGREEMENT,)),
        PairRisk(1, 0.2, (RiskReason.LOW_CONFIDENCE,)),
    )
    return store.record_verification(primary, secondary, alignment, risks, ())


def _project(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book | private", "/home/alice/book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    store.add_page(project_id, 1, "pages/1.png", 100, 200)
    _finish_job(
        store,
        tmp_path / "project.sqlite3",
        project_id,
        JobStage.IMPORT,
        page_index=None,
        state=JobState.SUCCEEDED,
        started_at="2026-01-01 00:00:00",
        finished_at="2026-01-01 00:00:01",
    )
    timings = {
        JobStage.PREPROCESS: 1,
        JobStage.PRIMARY_OCR: 5,
        JobStage.SECONDARY_OCR: 3,
        JobStage.VERIFY: 2,
    }
    for stage, seconds in timings.items():
        _finish_job(
            store,
            tmp_path / "project.sqlite3",
            project_id,
            stage,
            page_index=0,
            state=JobState.SUCCEEDED,
            started_at="2026-01-01 00:00:00",
            finished_at=f"2026-01-01 00:00:{seconds:02d}",
        )
    for stage, state, seconds in (
        (JobStage.PREPROCESS, JobState.SUCCEEDED, 1),
        (JobStage.PRIMARY_OCR, JobState.SUCCEEDED, 2),
        (JobStage.SECONDARY_OCR, JobState.FAILED, 4),
    ):
        _finish_job(
            store,
            tmp_path / "project.sqlite3",
            project_id,
            stage,
            page_index=1,
            state=state,
            started_at="2026-01-01 00:00:00",
            finished_at=f"2026-01-01 00:00:{seconds:02d}",
        )
    first_verification = _verification(store, project_id, 0)
    _verification(store, project_id, 1)
    store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="甲",
        actor="automatic",
        evidence_refs=(f"verification_run:{first_verification}",),
    )
    store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=1,
        selected_text="乙",
        actor="human",
        evidence_refs=(f"verification_run:{first_verification}",),
    )
    return store, project_id


def test_processing_report_aggregates_latest_attempt_spans_and_review_risk(
    tmp_path: Path,
) -> None:
    store, project_id = _project(tmp_path)

    report = build_processing_report(store, project_id)

    assert report.pages_total == 2
    assert report.ocr_complete_pages == 1
    assert report.failed_pages == 1
    assert report.running_pages == 0
    assert report.progress.pairs == 4
    assert report.progress.open_pairs == 2
    assert report.progress.open_high_risk == 1
    assert report.review_required_ratio == 0.5
    assert [(item.reason, item.count) for item in report.risk_reasons] == [
        ("low_confidence", 2),
        ("text_disagreement", 2),
    ]
    primary = next(item for item in report.stages if item.stage == "primary_ocr")
    secondary = next(item for item in report.stages if item.stage == "secondary_ocr")
    assert primary.observed_seconds == 7.0
    assert primary.succeeded == 2
    assert secondary.observed_seconds == 7.0
    assert (secondary.succeeded, secondary.failed) == (1, 1)
    assert report.observed_seconds == 19.0
    assert report.pages[0].status == "ocr_complete"
    assert report.pages[1].status == "failed"
    assert report.pages[0].observed_seconds == 11.0
    assert report.pages[0].open_pairs == 0
    assert report.pages[1].open_pairs == 2


def test_processing_report_json_is_stable_and_omits_private_paths_and_errors(
    tmp_path: Path,
) -> None:
    store, project_id = _project(tmp_path)

    first = processing_report_dict(build_processing_report(store, project_id))
    second = processing_report_dict(build_processing_report(store, project_id))
    encoded = json.dumps(first, ensure_ascii=False, sort_keys=True)

    assert first == second
    assert first["schema_version"] == 1
    assert first["review"]["review_required_ratio"] == 0.5
    assert first["timing"]["scope"] == "latest_attempt_timestamp_spans"
    assert "/home/alice" not in encoded
    assert "private failure" not in encoded
    assert "source_uri" not in encoded
    assert "last_error" not in encoded


def test_markdown_discloses_timing_limits_and_escapes_project_title(tmp_path: Path) -> None:
    store, project_id = _project(tmp_path)

    text = render_processing_report(build_processing_report(store, project_id))

    assert "Book \\| private" in text
    assert "latest recorded attempt" in text
    assert "whole-second" in text
    assert "not total book wall time" in text
    assert "primary_ocr" in text
    assert "text_disagreement" in text
    assert "Review required: 2 / 4 (50.00%)" in text
    assert "/home/alice" not in text


def test_empty_and_unfinished_projects_do_not_invent_zero_durations(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Empty", "empty.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    job_id = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    store.start_job(job_id)

    report = build_processing_report(store, project_id)

    assert report.observed_seconds is None
    assert report.timed_jobs == 0
    assert report.timing_unavailable_jobs == 1
    assert report.running_pages == 1
    assert report.pages[0].observed_seconds is None
    assert report.review_required_ratio == 0.0
    assert "Observed job time: —" in render_processing_report(report)


def test_retry_and_invalid_spans_do_not_invent_complete_engine_time(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Retries", "book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    _finish_job(
        store,
        tmp_path / "project.sqlite3",
        project_id,
        JobStage.PRIMARY_OCR,
        page_index=0,
        state=JobState.SUCCEEDED,
        started_at="2026-01-01 00:00:00",
        finished_at="2026-01-01 00:00:09",
    )
    job_id = next(
        job.id for job in store.list_jobs(project_id) if job.stage is JobStage.PRIMARY_OCR
    )
    store.reopen_job(job_id, "retry with new evidence")
    store.start_job(job_id)
    store.complete_job(job_id)
    _finish_job(
        store,
        tmp_path / "project.sqlite3",
        project_id,
        JobStage.SECONDARY_OCR,
        page_index=0,
        state=JobState.FAILED,
        started_at="2026-01-01 00:00:09",
        finished_at="2026-01-01 00:00:00",
    )
    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute(
            "UPDATE jobs SET started_at=?, finished_at=? WHERE id=?",
            ("2026-01-01 00:00:00", "2026-01-01 00:00:02", job_id),
        )

    report = build_processing_report(store, project_id)
    primary = next(item for item in report.stages if item.stage == "primary_ocr")

    assert primary.attempts == 2
    assert primary.observed_seconds == 2.0
    assert report.observed_seconds == 2.0
    assert report.timing_unavailable_jobs == 1


def test_report_cli_selects_one_project_and_emits_stable_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, project_id = _project(tmp_path)
    jobs_before = store.list_jobs(project_id)

    code = run(["report", "--store", str(tmp_path), "--project", str(project_id), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert captured.err == ""
    assert payload["project_id"] == project_id
    assert payload["pages"][0]["page_index"] == 0
    assert payload["stages"][0]["stage"] == "import"
    assert store.list_jobs(project_id) == jobs_before

    markdown_code = run(["report", "--store", str(tmp_path)])
    markdown = capsys.readouterr()
    assert markdown_code == 0
    assert markdown.err == ""
    assert markdown.out.startswith("# Processing Report\n")
    assert "Review required: 2 / 4 (50.00%)" in markdown.out


def test_report_cli_requires_project_selection_for_multiple_or_empty_stores(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()

    assert run(["report", "--store", str(tmp_path / "missing")]) == 2
    assert "no store found" in capsys.readouterr().err
    assert run(["report", "--store", str(tmp_path)]) == 2
    assert "no projects" in capsys.readouterr().err

    store.create_project("One", "one.pdf", "1")
    store.create_project("Two", "two.pdf", "2")
    assert run(["report", "--store", str(tmp_path)]) == 2
    assert "multiple projects" in capsys.readouterr().err
    assert run(["report", "--store", str(tmp_path), "--project", "999"]) == 2
    assert "unknown project" in capsys.readouterr().err
