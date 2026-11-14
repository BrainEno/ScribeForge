"""Analysis of structured JSONL pipeline event logs (issue #69).

`scribeforge events <file.jsonl>` reads a trace written by
``process --events-file`` and answers the operational questions a multi-hour
run raises: which stage (engine) ate the time, which pages were slowest, why
did pages fail, and how long the book took overall. Everything here is pure
over already-read lines; malformed lines (a crash mid-write) are skipped.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Event:
    """One parsed JSONL event.

    ``fields`` holds everything except the envelope keys ``event`` and
    ``run``; the run id is exposed separately as :attr:`run`.
    """

    event: str
    fields: dict[str, Any]
    run: str | None = None


@dataclass(frozen=True, slots=True)
class StageTiming:
    stage: str
    count: int
    total_seconds: float
    mean_seconds: float


@dataclass(frozen=True, slots=True)
class PageTiming:
    page_index: int
    elapsed_seconds: float
    failed: bool


@dataclass(frozen=True, slots=True)
class EventAnalysis:
    event_counts: dict[str, int]
    stages: tuple[StageTiming, ...]
    pages: tuple[PageTiming, ...]
    failed_pages: list[int]
    failure_reasons: dict[str, int]
    in_flight_pages: tuple[int, ...]
    book_elapsed_seconds: float | None
    aborted: bool
    succeeded: int | None
    failed: int | None
    #: Declared book size and effective worker count from book_started.
    book_pages: int | None
    book_workers: int | None

    @property
    def slowest_stage(self) -> str | None:
        """Stage with the largest total time — the bottleneck (e.g. slower engine)."""
        if not self.stages:
            return None
        return max(self.stages, key=lambda s: s.total_seconds).stage

    def engine_comparison(self) -> tuple[StageTiming, StageTiming] | None:
        """The primary vs secondary OCR stage timings, when both are present."""
        by_stage = {s.stage: s for s in self.stages}
        primary = by_stage.get("primary_ocr")
        secondary = by_stage.get("secondary_ocr")
        if primary is None or secondary is None:
            return None
        return primary, secondary

    @property
    def total_page_seconds(self) -> float:
        return round(sum(p.elapsed_seconds for p in self.pages), 6)

    @property
    def pages_per_minute(self) -> float | None:
        elapsed = self.book_elapsed_seconds
        if not elapsed or elapsed <= 0 or not self.pages:
            return None
        return round(len(self.pages) / (elapsed / 60.0), 4)

    def slowest_pages(self, n: int) -> list[tuple[int, float]]:
        """Top-``n`` pages by elapsed time, slowest first."""
        ranked = sorted(
            ((p.page_index, p.elapsed_seconds) for p in self.pages),
            key=lambda item: item[1],
            reverse=True,
        )
        return ranked[: max(0, n)]


def parse_events(lines: Iterable[str]) -> list[Event]:
    """Parse JSONL lines into events; malformed or event-less lines are skipped."""
    events: list[Event] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        event = payload.get("event")
        if not isinstance(event, str) or not event:
            continue
        fields = {key: value for key, value in payload.items() if key not in ("event", "run")}
        run = payload.get("run")
        events.append(Event(event, fields, run if isinstance(run, str) else None))
    return events


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def analyze_events(events: Sequence[Event]) -> EventAnalysis:
    """Aggregate parsed events into an :class:`EventAnalysis`."""
    counts: dict[str, int] = {}
    stage_totals: dict[str, float] = {}
    stage_counts: dict[str, int] = {}
    page_seconds: dict[int, float] = {}
    failed_pages: list[int] = []
    failure_reasons: dict[str, int] = {}
    started_pages: set[int] = set()
    book_elapsed: float | None = None
    aborted = False
    succeeded: int | None = None
    failed: int | None = None
    book_pages: int | None = None
    book_workers: int | None = None

    for event in events:
        counts[event.event] = counts.get(event.event, 0) + 1
        fields = event.fields
        if event.event == "stage_finished":
            stage = fields.get("stage")
            elapsed = _number(fields.get("elapsed_seconds"))
            if isinstance(stage, str) and elapsed is not None:
                stage_totals[stage] = stage_totals.get(stage, 0.0) + elapsed
                stage_counts[stage] = stage_counts.get(stage, 0) + 1
        elif event.event == "page_started":
            page_index = fields.get("page_index")
            if isinstance(page_index, int):
                started_pages.add(page_index)
        elif event.event == "page_finished":
            page_index = fields.get("page_index")
            elapsed = _number(fields.get("elapsed_seconds"))
            if isinstance(page_index, int) and elapsed is not None:
                page_seconds[page_index] = elapsed
                started_pages.discard(page_index)
        elif event.event == "page_failed":
            page_index = fields.get("page_index")
            elapsed = _number(fields.get("elapsed_seconds"))
            if isinstance(page_index, int):
                failed_pages.append(page_index)
                if elapsed is not None:
                    page_seconds[page_index] = elapsed
                started_pages.discard(page_index)
                reason = fields.get("error")
                reason = reason.strip() if isinstance(reason, str) and reason.strip() else "unknown"
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        elif event.event == "book_started":
            pages = fields.get("pages")
            workers = fields.get("max_workers")
            if isinstance(pages, int) and not isinstance(pages, bool):
                book_pages = pages
            if isinstance(workers, int) and not isinstance(workers, bool):
                book_workers = workers
        elif event.event in ("book_finished", "book_aborted"):
            elapsed = _number(fields.get("elapsed_seconds"))
            if elapsed is not None:
                book_elapsed = elapsed
            if event.event == "book_aborted":
                aborted = True
            succeeded = (
                fields.get("succeeded") if isinstance(fields.get("succeeded"), int) else None
            )
            failed = fields.get("failed") if isinstance(fields.get("failed"), int) else None

    stages = tuple(
        StageTiming(
            stage=stage,
            count=stage_counts[stage],
            total_seconds=round(stage_totals[stage], 6),
            mean_seconds=round(stage_totals[stage] / stage_counts[stage], 6),
        )
        for stage in sorted(stage_totals)
    )
    pages = tuple(
        PageTiming(page_index=index, elapsed_seconds=seconds, failed=index in failed_pages)
        for index, seconds in sorted(page_seconds.items())
    )
    return EventAnalysis(
        event_counts=dict(sorted(counts.items())),
        stages=stages,
        pages=pages,
        failed_pages=sorted(failed_pages),
        failure_reasons=dict(sorted(failure_reasons.items())),
        in_flight_pages=tuple(sorted(started_pages)),
        book_elapsed_seconds=book_elapsed,
        aborted=aborted,
        succeeded=succeeded,
        failed=failed,
        book_pages=book_pages,
        book_workers=book_workers,
    )


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def render_events_markdown(analysis: EventAnalysis, *, top: int = 10) -> str:
    """Render an analysis as a Markdown report."""
    lines = [
        "# Event log analysis",
        "",
        f"- book elapsed: {_seconds(analysis.book_elapsed_seconds)} s",
        f"- outcome: {'aborted' if analysis.aborted else 'finished'}",
        f"- pages succeeded: {analysis.succeeded if analysis.succeeded is not None else '-'}",
        f"- pages failed: {analysis.failed if analysis.failed is not None else len(analysis.failed_pages)}",
        f"- in-flight pages at end: {', '.join(str(p) for p in analysis.in_flight_pages) or '-'}",
        "",
        "## Event counts",
        "",
        "| Event | Count |",
        "| --- | ---: |",
    ]
    for event, count in analysis.event_counts.items():
        lines.append(f"| {event} | {count} |")
    lines += [
        "",
        "## Stage timings",
        "",
        "| Stage | Count | Total s | Mean s |",
        "| --- | ---: | ---: | ---: |",
    ]
    for stage in analysis.stages:
        lines.append(
            f"| {stage.stage} | {stage.count} | {stage.total_seconds:.3f} | {stage.mean_seconds:.3f} |"
        )
    if analysis.slowest_stage is not None:
        lines += ["", f"**Bottleneck stage: `{analysis.slowest_stage}`**"]
    comparison = analysis.engine_comparison()
    if comparison is not None:
        primary, secondary = comparison
        lines += [
            "",
            "## Engine comparison (primary vs secondary OCR)",
            "",
            "| Engine | Count | Total s | Mean s |",
            "| --- | ---: | ---: | ---: |",
            f"| primary | {primary.count} | {primary.total_seconds:.3f} | {primary.mean_seconds:.3f} |",
            f"| secondary | {secondary.count} | {secondary.total_seconds:.3f} | {secondary.mean_seconds:.3f} |",
        ]
        slower = "secondary" if secondary.total_seconds > primary.total_seconds else "primary"
        lines += ["", f"**Slower engine: {slower}**"]
    if analysis.pages_per_minute is not None:
        lines += ["", f"- throughput: {analysis.pages_per_minute:.2f} pages/min"]
    lines += [
        "",
        f"## Slowest pages (top {top})",
        "",
        "| Page | Elapsed s | Failed |",
        "| ---: | ---: | --- |",
    ]
    for page_index, elapsed in analysis.slowest_pages(top):
        failed = next((p.failed for p in analysis.pages if p.page_index == page_index), False)
        lines.append(f"| {page_index} | {elapsed:.3f} | {'yes' if failed else 'no'} |")
    if analysis.failed_pages:
        lines += ["", "## Failures", "", "| Reason | Count |", "| --- | ---: |"]
        for reason, count in analysis.failure_reasons.items():
            lines.append(f"| {reason} | {count} |")
        lines.append("")
    lines.append("")
    return "\n".join(lines)


def events_json(
    analysis: EventAnalysis,
    *,
    top: int = 10,
    estimate_remaining_pages: int | None = None,
) -> str:
    """Stable JSON rendering of an analysis for scripting.

    When ``estimate_remaining_pages`` is given, an ``estimate`` block with the
    projected remaining seconds (observed mean page time / workers) is added.
    """
    payload: dict[str, Any] = {
        "book_elapsed_seconds": analysis.book_elapsed_seconds,
        "aborted": analysis.aborted,
        "succeeded": analysis.succeeded,
        "failed": analysis.failed,
        "event_counts": analysis.event_counts,
        "stages": [
            {
                "stage": s.stage,
                "count": s.count,
                "total_seconds": s.total_seconds,
                "mean_seconds": s.mean_seconds,
            }
            for s in analysis.stages
        ],
        "slowest_stage": analysis.slowest_stage,
        "total_page_seconds": analysis.total_page_seconds,
        "pages_per_minute": analysis.pages_per_minute,
        "engine_comparison": (
            None
            if analysis.engine_comparison() is None
            else {
                "primary": {
                    "count": analysis.engine_comparison()[0].count,  # type: ignore[index]
                    "total_seconds": analysis.engine_comparison()[0].total_seconds,  # type: ignore[index]
                    "mean_seconds": analysis.engine_comparison()[0].mean_seconds,  # type: ignore[index]
                },
                "secondary": {
                    "count": analysis.engine_comparison()[1].count,  # type: ignore[index]
                    "total_seconds": analysis.engine_comparison()[1].total_seconds,  # type: ignore[index]
                    "mean_seconds": analysis.engine_comparison()[1].mean_seconds,  # type: ignore[index]
                },
            }
        ),
        "slowest_pages": [
            {"page_index": index, "elapsed_seconds": elapsed}
            for index, elapsed in analysis.slowest_pages(top)
        ],
        "failed_pages": analysis.failed_pages,
        "failure_reasons": analysis.failure_reasons,
        "in_flight_pages": list(analysis.in_flight_pages),
        "book_pages": analysis.book_pages,
        "book_workers": analysis.book_workers,
    }
    if estimate_remaining_pages is not None:
        from scribeforge.domain.scheduling import estimate_remaining_seconds

        observed = [page.elapsed_seconds for page in analysis.pages]
        workers = analysis.book_workers or 1
        payload["estimate"] = {
            "remaining_pages": estimate_remaining_pages,
            "workers": workers,
            "observed_pages": len(observed),
            "estimated_remaining_seconds": round(
                estimate_remaining_seconds(
                    observed, remaining_pages=estimate_remaining_pages, workers=workers
                ),
                6,
            ),
        }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
