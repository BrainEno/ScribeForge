"""Deterministic, read-only processing reports built from persisted evidence."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

from scribeforge.domain.jobs import OCR_STAGES, JobRecord, JobStage, JobState
from scribeforge.pipeline.rounds import ProgressReport, open_pairs, progress
from scribeforge.storage.sqlite import SQLiteStore

_TIMING_SCOPE = "latest_attempt_timestamp_spans"


@dataclass(frozen=True, slots=True)
class ReasonCount:
    reason: str
    count: int


@dataclass(frozen=True, slots=True)
class RiskBucket:
    """One bucket of the risk-score distribution: scores in [low, high]."""

    low: float
    high: float
    count: int


def risk_score_distribution(
    scores: Sequence[float], *, bucket_count: int = 5
) -> tuple[RiskBucket, ...]:
    """Bucket risk scores into ``bucket_count`` equal-width [0, 1] buckets.

    Score 1.0 belongs to the last bucket. Scores outside [0, 1] are rejected:
    a risk score outside the unit interval is a bug, not data.
    """
    if not 1 <= bucket_count <= 20:
        raise ValueError(f"bucket_count must be in [1, 20], got {bucket_count}")
    for score in scores:
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"risk score out of range [0, 1]: {score}")
    width = 1.0 / bucket_count
    counts = [0] * bucket_count
    for score in scores:
        # The epsilon keeps exact bucket edges (0.2, 0.4, ...) in their upper
        # bucket despite binary floating point (0.6 / 0.2 == 2.999...).
        index = min(int(score / width + 1e-9), bucket_count - 1)
        counts[index] += 1
    return tuple(
        RiskBucket(low=round(i * width, 6), high=round((i + 1) * width, 6), count=counts[i])
        for i in range(bucket_count)
    )


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
    #: Risk-score distribution over all latest-verification pairs (issue #69).
    risk_distribution: tuple[RiskBucket, ...] = ()

    @property
    def high_risk_pairs_total(self) -> int:
        return sum(page.high_risk_pairs for page in self.pages)

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
    risk_scores: list[float] = []
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
            risk_scores.extend(risk.score for risk in verification.risks)
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
        risk_distribution=risk_score_distribution(risk_scores),
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
        "risk_distribution": [
            {"low": b.low, "high": b.high, "count": b.count} for b in report.risk_distribution
        ],
        "high_risk_pairs_total": report.high_risk_pairs_total,
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


def processing_report_json(
    report: ProcessingReport, *, event_summary: dict[str, object] | None = None
) -> str:
    payload = processing_report_dict(report)
    if event_summary is not None:
        # Additive key: existing schema-v1 consumers ignore unknown keys.
        payload["event_summary"] = event_summary
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def render_event_summary(summary: dict[str, Any]) -> str:
    """Render a summarize_events() dict as a Markdown report section."""
    lines = ["## Event log summary", ""]
    events = cast(dict[str, int], summary.get("events") or {})
    if events:
        rendered = ", ".join(f"`{k}` x{v}" for k, v in sorted(events.items()))
        lines.append(f"- events: {rendered}")
    failed = cast(list[int], summary.get("failed_pages") or [])
    lines.append(f"- failed pages: {', '.join(str(i) for i in failed) if failed else 'none'}")
    elapsed = summary.get("book_elapsed_seconds")
    lines.append(f"- book elapsed: {'—' if elapsed is None else f'{float(elapsed):.3f}s'}")
    total = summary.get("total_page_seconds")
    lines.append(f"- summed page time: {'—' if total is None else f'{float(total):.3f}s'}")
    slowest = cast(dict[str, Any] | None, summary.get("slowest_page"))
    if slowest:
        lines.append(
            f"- slowest page: #{slowest['page_index']} ({float(slowest['elapsed_seconds']):.3f}s)"
        )
    lines.append("")
    return "\n".join(lines)


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

    lines += ["", "## Risk distribution", ""]
    total_pairs = sum(bucket.count for bucket in report.risk_distribution)
    if total_pairs:
        lines += ["| Score range | Pairs | Share |"]
        lines.append("| --- | ---: | ---: |")
        max_count = max(bucket.count for bucket in report.risk_distribution)
        for bucket in report.risk_distribution:
            share = bucket.count / total_pairs
            bar = "#" * round(20 * bucket.count / max_count) if max_count else ""
            lines.append(
                f"| [{bucket.low:.1f}, {bucket.high:.1f}] | {bucket.count} | {share:.1%} {bar} |"
            )
        lines.append("")
        lines.append(f"- high-risk pairs (score >= 0.6): {report.high_risk_pairs_total}")
    else:
        lines.append("No scored verification pairs yet.")
    lines.append("")

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


@dataclass(frozen=True, slots=True)
class ReportComparison:
    """Field-level delta between two processing-report JSON payloads."""

    pages_total_delta: int
    ocr_complete_delta: int
    failed_pages_delta: int
    review_ratio_delta: float
    observed_seconds_delta: float | None
    #: Bucket-index -> count delta of the risk distribution (empty when either
    #: side has no distribution).
    risk_bucket_deltas: dict[int, int]


def _report_number(payload: dict[str, Any], key: str) -> float:
    value = payload.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def compare_report_dicts(old: dict[str, Any], new: dict[str, Any]) -> ReportComparison:
    """Compare two schema-v1 processing-report dicts (old -> new).

    Unknown or missing fields count as zero; a different schema version is
    rejected so a v1 consumer never silently diffs a foreign payload.
    """
    for side, payload in (("old", old), ("new", new)):
        if not isinstance(payload, dict):
            raise TypeError(f"{side} report payload must be a JSON object")
        if payload.get("schema_version") != 1:
            raise ValueError(
                f"{side} report is not schema_version 1: {payload.get('schema_version')!r}"
            )
    old_review = cast(dict[str, Any], old.get("review") or {})
    new_review = cast(dict[str, Any], new.get("review") or {})
    old_timing = cast(dict[str, Any], old.get("timing") or {})
    new_timing = cast(dict[str, Any], new.get("timing") or {})
    old_seconds = old_timing.get("observed_seconds")
    new_seconds = new_timing.get("observed_seconds")
    deltas: dict[int, int] = {}
    old_dist = old.get("risk_distribution")
    new_dist = new.get("risk_distribution")
    if isinstance(old_dist, list) and isinstance(new_dist, list):
        for index in range(max(len(old_dist), len(new_dist))):
            old_count = old_dist[index]["count"] if index < len(old_dist) else 0
            new_count = new_dist[index]["count"] if index < len(new_dist) else 0
            delta = int(new_count) - int(old_count)
            if delta:
                deltas[index] = delta
    return ReportComparison(
        pages_total_delta=int(
            _report_number(new, "pages_total") - _report_number(old, "pages_total")
        ),
        ocr_complete_delta=int(
            _report_number(new, "ocr_complete_pages") - _report_number(old, "ocr_complete_pages")
        ),
        failed_pages_delta=int(
            _report_number(new, "failed_pages") - _report_number(old, "failed_pages")
        ),
        review_ratio_delta=float(new_review.get("review_required_ratio", 0.0))
        - float(old_review.get("review_required_ratio", 0.0)),
        observed_seconds_delta=(
            None
            if old_seconds is None or new_seconds is None
            else float(new_seconds) - float(old_seconds)
        ),
        risk_bucket_deltas=deltas,
    )


def _delta_text(delta: float) -> str:
    if delta > 0:
        return f"+{delta:g}"
    return f"{delta:g}"


def render_comparison_markdown(comparison: ReportComparison) -> str:
    """Render a report comparison as Markdown."""
    lines = [
        "# Report comparison",
        "",
        "| Metric | Delta (new - old) |",
        "| --- | ---: |",
        f"| pages_total | {_delta_text(comparison.pages_total_delta)} |",
        f"| ocr_complete_pages | {_delta_text(comparison.ocr_complete_delta)} |",
        f"| failed_pages | {_delta_text(comparison.failed_pages_delta)} |",
        f"| review_required_ratio | {_delta_text(comparison.review_ratio_delta)} |",
        (
            "| observed_seconds | "
            + (
                "—"
                if comparison.observed_seconds_delta is None
                else _delta_text(comparison.observed_seconds_delta)
            )
            + " |"
        ),
    ]
    if comparison.risk_bucket_deltas:
        lines += ["", "## Risk distribution delta", "", "| Bucket | Delta |", "| --- | ---: |"]
        for index in sorted(comparison.risk_bucket_deltas):
            lines.append(
                f"| bucket {index} | {_delta_text(comparison.risk_bucket_deltas[index])} |"
            )
    lines.append("")
    return "\n".join(lines)
