"""Deterministic Markdown report for benchmark runs.

The report keeps the aggregate table for comparison and one table per variant for
every case, so an improvement on easy pages cannot hide a regression on a
difficult page class. Timestamps are injected instead of read from the clock, so
two runs over the same results render identically.
"""

from __future__ import annotations

from collections.abc import Sequence

from scribeforge.benchmark.runner import BenchmarkReport, CaseScore


def _direction_of(case: CaseScore) -> str:
    """Return the case's declared direction, or ``horizontal-lr`` when unstated.

    A corpus that predates the direction field is horizontal: that is the only
    thing the existing six pages are, and silently grouping them under "unknown"
    would hide the very split the vertical gate needs to see.
    """

    return case.direction or "horizontal-lr"


def _mean(values: Sequence[float], empty: float) -> float:
    return sum(values) / len(values) if values else empty


def _direction_rows(reports: Sequence[BenchmarkReport]) -> list[str]:
    """Aggregate CER per writing direction across every variant."""

    directions = sorted({_direction_of(case) for report in reports for case in report.cases})
    if len(directions) < 2:
        return []
    lines = [
        "## Results by writing direction",
        "",
        (
            "| Variant | Direction | Cases | Mean CER | Unresolved pairs "
            "| Reading-order errors |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        for direction in directions:
            cases = [case for case in report.cases if _direction_of(case) == direction]
            if not cases:
                continue
            unresolved = sum(case.unresolved_pairs or 0 for case in cases)
            lines.append(
                f"| {report.engine} | {direction} | {len(cases)} | "
                f"{_mean([case.cer for case in cases], 0.0):.4f} | {unresolved} | "
                f"{sum(case.reading_order_errors for case in cases)} |"
            )
    lines.append("")
    return lines


def render_report(
    reports: Sequence[BenchmarkReport],
    *,
    corpus: str,
    manifest_sha256: str,
    generated_at: str,
) -> str:
    """Render one Markdown report for every scored variant."""

    lines = [
        "# Benchmark Report",
        "",
        f"- corpus: `{corpus}`",
        f"- manifest sha256: `{manifest_sha256}`",
        f"- generated: {generated_at}",
        f"- variants: {len(reports)}",
        "",
    ]
    if not reports:
        lines += ["No variants were scored.", ""]
        return "\n".join(lines)

    lines += [
        "## Variants",
        "",
        (
            "| Variant | Cases | Mean CER | Mean WER | Punctuation | Missing | Extra "
            "| Reading-order errors | Accepted pairs | Unresolved pairs | Elapsed s | Pages/min |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        aggregate = report.aggregate
        lines.append(
            f"| {report.engine} | {aggregate.case_count} | "
            f"{aggregate.mean_cer:.4f} | {aggregate.mean_wer:.4f} | "
            f"{aggregate.mean_punctuation_accuracy:.4f} | "
            f"{aggregate.missing_lines} | {aggregate.extra_lines} | "
            f"{aggregate.reading_order_errors} | "
            f"{aggregate.accepted_pairs if aggregate.accepted_pairs is not None else '-'} | "
            f"{aggregate.unresolved_pairs if aggregate.unresolved_pairs is not None else '-'} | "
            f"{aggregate.elapsed_seconds:.3f} | {aggregate.pages_per_minute:.2f} |"
        )

    direction_rows = _direction_rows(reports)
    if direction_rows:
        lines += [""] + direction_rows

    lines += ["", "## Per-case results", ""]
    for report in reports:
        lines += [
            f"### {report.engine}",
            "",
            (
                "| Case | Language | Tags | CER | WER | Punctuation | Missing | Extra "
                "| Reading-order errors | Accepted pairs | Unresolved pairs | Seconds |"
            ),
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for case in report.cases:
            tags = ",".join(case.tags) if case.tags else "-"
            lines.append(
                f"| {case.case_id} | {case.language} | {tags} | {case.cer:.4f} | "
                f"{case.wer:.4f} | {case.punctuation_accuracy:.4f} | "
                f"{case.missing_lines} | {case.extra_lines} | "
                f"{case.reading_order_errors} | "
                f"{case.accepted_pairs if case.accepted_pairs is not None else '-'} | "
                f"{case.unresolved_pairs if case.unresolved_pairs is not None else '-'} | "
                f"{case.elapsed_seconds:.3f} |"
            )
        lines.append("")
    return "\n".join(lines)
