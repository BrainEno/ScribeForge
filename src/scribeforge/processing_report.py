"""Deterministic, read-only processing reports built from persisted evidence."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

from scribeforge.domain.jobs import OCR_STAGES, JobRecord, JobStage, JobState
from scribeforge.pipeline.rounds import ProgressReport, open_pairs, progress
from scribeforge.storage.sqlite import SQLiteStore

_TIMING_SCOPE = "latest_attempt_timestamp_spans"


@dataclass(frozen=True, slots=True)
class ReasonCount:
    reason: str
    count: int


@dataclass(frozen=True, slots=True)
class StageReport:
    stage: str
    jobs: int
    pending: int
    running: int
    succeeded: int
    failed: int
    attempts: int
    timed_jobs: int
    timing_unavailable_jobs: int
    observed_seconds: float | None


@dataclass(frozen=True, slots=True)
class PageStageTiming:
    stage: str
    state: str
    attempts: int
    observed_seconds: float | None


@dataclass(frozen=True, slots=True)
class PageReport:
    page_index: int
    status: str
    aligned_pairs: int
    high_risk_pairs: int
    open_pairs: int
    risk_reasons: tuple[ReasonCount, ...]
    stage_timings: tuple[PageStageTiming, ...]

    @property
    def observed_seconds(self) -> float | None:
        measured = [
            timing.observed_seconds
            for timing in self.stage_timings
            if timing.observed_seconds is not None
        ]
        return sum(measured) if measured else None

    def stage_seconds(self, stage: JobStage) -> float | None:
        timing = next((item for item in self.stage_timings if item.stage == stage.value), None)
        return None if timing is None else timing.observed_seconds


@dataclass(frozen=True, slots=True)
class ProcessingReport:
    project_id: int
    title: str
    pages_total: int
    ocr_complete_pages: int
    failed_pages: int
    running_pages: int
    progress: ProgressReport
    risk_reasons: tuple[ReasonCount, ...]
    stages: tuple[StageReport, ...]
    pages: tuple[PageReport, ...]
    observed_seconds: float | None
    timed_jobs: int
    timing_unavailable_jobs: int

    @property
    def review_required_ratio(self) -> float:
        if not self.progress.pairs:
            return 0.0
        return self.progress.open_pairs / self.progress.pairs


def _observed_seconds(job: JobRecord) -> float | None:
    """Return the latest completed attempt's persisted timestamp span.

    SQLite job history intentionally keeps only the latest start/finish pair.
    Pending/running jobs, incomplete intervals, malformed values and negative
    spans have no measurable duration; they are excluded rather than scored as 0.
    """

    if (
        job.state not in {JobState.SUCCEEDED, JobState.FAILED}
        or job.started_at is None
        or job.finished_at is None
    ):
        return None
    try:
        started = datetime.fromisoformat(job.started_at)
        finished = datetime.fromisoformat(job.finished_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        seconds = (finished - started).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None
    return seconds if seconds >= 0 else None


def _stage_report(stage: JobStage, jobs: list[JobRecord]) -> StageReport:
    durations = [_observed_seconds(job) for job in jobs]
    measured = [duration for duration in durations if duration is not None]
    return StageReport(
        stage=stage.value,
        jobs=len(jobs),
        pending=sum(job.state is JobState.PENDING for job in jobs),
        running=sum(job.state is JobState.RUNNING for job in jobs),
        succeeded=sum(job.state is JobState.SUCCEEDED for job in jobs),
        failed=sum(job.state is JobState.FAILED for job in jobs),
        attempts=sum(job.attempts for job in jobs),
        timed_jobs=len(measured),
        timing_unavailable_jobs=len(jobs) - len(measured),
        observed_seconds=sum(measured) if measured else None,
    )


def _reason_counts(counter: Counter[str]) -> tuple[ReasonCount, ...]:
    return tuple(ReasonCount(reason, counter[reason]) for reason in sorted(counter))


def build_processing_report(
    store: SQLiteStore, project_id: int, *, high_risk: float = 0.6
) -> ProcessingReport:
    """Aggregate one project without modifying jobs, evidence or source assets."""

    project = next((item for item in store.list_projects() if item.id == project_id), None)
    if project is None:
        raise KeyError(f"unknown project: {project_id}")
    page_records = store.list_pages(project_id)
    jobs = list(store.list_jobs(project_id))
    review_progress = progress(store, project_id, high_risk=high_risk)
    unresolved = open_pairs(store, project_id)
    open_by_page = Counter(ref.page_index for ref in unresolved)

    durations = [_observed_seconds(job) for job in jobs]
    measured = [duration for duration in durations if duration is not None]
    jobs_by_stage = {
        stage: [job for job in jobs if job.stage is stage]
        for stage in sorted(JobStage, key=lambda item: item.order)
    }
    stages = tuple(_stage_report(stage, jobs_by_stage[stage]) for stage in jobs_by_stage)

    all_reasons: Counter[str] = Counter()
    pages: list[PageReport] = []
    ocr_complete_pages = 0
    failed_pages = 0
    running_pages = 0
    for page in page_records:
        page_jobs = [job for job in jobs if job.page_index == page.page_index]
        states = {job.stage: job.state for job in page_jobs}
        ocr_complete = all(states.get(stage) is JobState.SUCCEEDED for stage in OCR_STAGES)
        has_failed = any(job.state is JobState.FAILED for job in page_jobs)
        has_running = any(job.state is JobState.RUNNING for job in page_jobs)
        ocr_complete_pages += int(ocr_complete)
        failed_pages += int(has_failed)
        running_pages += int(has_running)
        if has_failed:
            status = "failed"
        elif has_running:
            status = "running"
        elif ocr_complete:
            status = "ocr_complete"
        else:
            status = "incomplete"

        page_reasons: Counter[str] = Counter()
        aligned_pairs = 0
        high_risk_pairs = 0
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            pass
        else:
            verification = store.load_verification(verification_id)
            aligned_pairs = len(verification.alignment.pairs)
            high_risk_pairs = sum(risk.score >= high_risk for risk in verification.risks)
            page_reasons.update(
                reason.value for risk in verification.risks for reason in risk.reasons
            )
        all_reasons.update(page_reasons)
        timings = tuple(
            PageStageTiming(
                stage=job.stage.value,
                state=job.state.value,
                attempts=job.attempts,
                observed_seconds=_observed_seconds(job),
            )
            for job in sorted(page_jobs, key=lambda item: item.stage.order)
        )
        pages.append(
            PageReport(
                page_index=page.page_index,
                status=status,
                aligned_pairs=aligned_pairs,
                high_risk_pairs=high_risk_pairs,
                open_pairs=open_by_page[page.page_index],
                risk_reasons=_reason_counts(page_reasons),
                stage_timings=timings,
            )
        )

    return ProcessingReport(
        project_id=project_id,
        title=project.title,
        pages_total=len(page_records),
        ocr_complete_pages=ocr_complete_pages,
        failed_pages=failed_pages,
        running_pages=running_pages,
        progress=review_progress,
        risk_reasons=_reason_counts(all_reasons),
        stages=stages,
        pages=tuple(pages),
        observed_seconds=sum(measured) if measured else None,
        timed_jobs=len(measured),
        timing_unavailable_jobs=len(jobs) - len(measured),
    )


def processing_report_dict(report: ProcessingReport) -> dict[str, object]:
    """Return the stable, source-path-free JSON representation (schema v1)."""

    return {
        "schema_version": 1,
        "project_id": report.project_id,
        "title": report.title,
        "pages_total": report.pages_total,
        "ocr_complete_pages": report.ocr_complete_pages,
        "failed_pages": report.failed_pages,
        "running_pages": report.running_pages,
        "review": {
            "pairs": report.progress.pairs,
            "accepted_automatic": report.progress.accepted_automatic,
            "accepted_vlm": report.progress.accepted_vlm,
            "accepted_human": report.progress.accepted_human,
            "open_pairs": report.progress.open_pairs,
            "open_high_risk": report.progress.open_high_risk,
            "open_blocked": report.progress.open_blocked,
            "review_required_ratio": report.review_required_ratio,
        },
        "risk_reasons": {item.reason: item.count for item in report.risk_reasons},
        "timing": {
            "scope": _TIMING_SCOPE,
            "observed_seconds": report.observed_seconds,
            "timed_jobs": report.timed_jobs,
            "unavailable_jobs": report.timing_unavailable_jobs,
        },
        "stages": [
            {
                "stage": stage.stage,
                "jobs": stage.jobs,
                "pending": stage.pending,
                "running": stage.running,
                "succeeded": stage.succeeded,
                "failed": stage.failed,
                "attempts": stage.attempts,
                "timed_jobs": stage.timed_jobs,
                "timing_unavailable_jobs": stage.timing_unavailable_jobs,
                "observed_seconds": stage.observed_seconds,
            }
            for stage in report.stages
        ],
        "pages": [
            {
                "page_index": page.page_index,
                "status": page.status,
                "aligned_pairs": page.aligned_pairs,
                "high_risk_pairs": page.high_risk_pairs,
                "open_pairs": page.open_pairs,
                "risk_reasons": {item.reason: item.count for item in page.risk_reasons},
                "observed_seconds": page.observed_seconds,
                "stage_timings": [
                    {
                        "stage": timing.stage,
                        "state": timing.state,
                        "attempts": timing.attempts,
                        "observed_seconds": timing.observed_seconds,
                    }
                    for timing in page.stage_timings
                ],
            }
            for page in report.pages
        ],
    }


def processing_report_json(report: ProcessingReport) -> str:
    return (
        json.dumps(processing_report_dict(report), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    )


def _cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def render_processing_report(report: ProcessingReport) -> str:
    """Render deterministic Markdown with explicit timing and review semantics."""

    ratio = report.review_required_ratio * 100
    observed = "—" if report.observed_seconds is None else f"{report.observed_seconds:.3f} s"
    lines = [
        "# Processing Report",
        "",
        f"- project: `{_cell(report.title)}` (ID {report.project_id})",
        f"- pages: {report.pages_total}",
        f"- OCR-complete pages: {report.ocr_complete_pages}",
        f"- pages with failed jobs: {report.failed_pages}",
        f"- pages with running jobs: {report.running_pages}",
        (
            f"- Review required: {report.progress.open_pairs} / "
            f"{report.progress.pairs} ({ratio:.2f}%)"
        ),
        (
            f"- Observed job time: {observed} "
            f"across {report.timed_jobs} timed job(s); "
            f"{report.timing_unavailable_jobs} unavailable"
        ),
        "",
        (
            "> Timing covers only the latest recorded attempt with complete, valid "
            "whole-second SQLite timestamps. Earlier retries, running jobs and "
            "unavailable intervals are excluded. Stage spans are not total book wall time."
        ),
        (
            "> Review counts use the production non-furniture queue. Risk reasons are "
            "occurrences on the latest verification pairs; one pair can have several reasons."
        ),
        "",
        "## Job stages",
        "",
        (
            "| Stage | Jobs | Succeeded | Failed | Pending | Running | Attempts "
            "| Timed jobs | Unavailable | Observed s |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stage in report.stages:
        lines.append(
            f"| {stage.stage} | {stage.jobs} | {stage.succeeded} | {stage.failed} "
            f"| {stage.pending} | {stage.running} | {stage.attempts} "
            f"| {stage.timed_jobs} | {stage.timing_unavailable_jobs} "
            f"| {_seconds(stage.observed_seconds)} |"
        )

    lines += ["", "## Risk reasons", ""]
    if report.risk_reasons:
        lines += ["| Reason | Occurrences |", "| --- | ---: |"]
        lines.extend(f"| {item.reason} | {item.count} |" for item in report.risk_reasons)
    else:
        lines.append("No verification risk reasons recorded.")

    lines += [
        "",
        "## Pages",
        "",
        (
            "| Page (1-based) | Status | Primary OCR s | Secondary OCR s "
            "| Observed total s | Aligned pairs | High risk | Open review | Risk reasons |"
        ),
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for page in report.pages:
        reasons = ", ".join(f"{item.reason}={item.count}" for item in page.risk_reasons) or "—"
        lines.append(
            f"| {page.page_index + 1} | {page.status} "
            f"| {_seconds(page.stage_seconds(JobStage.PRIMARY_OCR))} "
            f"| {_seconds(page.stage_seconds(JobStage.SECONDARY_OCR))} "
            f"| {_seconds(page.observed_seconds)} | {page.aligned_pairs} "
            f"| {page.high_risk_pairs} | {page.open_pairs} | {reasons} |"
        )
    lines.append("")
    return "\n".join(lines)
