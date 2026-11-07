"""Deterministic Markdown report for benchmark runs.

The report keeps the aggregate table for comparison and one table per variant for
every case, so an improvement on easy pages cannot hide a regression on a
difficult page class. Timestamps are injected instead of read from the clock, so
two runs over the same results render identically.
"""

from __future__ import annotations

from collections.abc import Sequence

from scribeforge.benchmark.runner import AggregateScore, BenchmarkReport, CaseScore


def _direction_of(case: CaseScore) -> str:
    """Return the case's declared direction, or ``horizontal-lr`` when unstated.

    A corpus that predates the direction field is horizontal: that is the only
    thing the existing six pages are, and silently grouping them under "unknown"
    would hide the very split the vertical gate needs to see.
    """

    return case.direction or "horizontal-lr"


def _mean(values: Sequence[float], empty: float) -> float:
    return sum(values) / len(values) if values else empty


def _case_wer(case: CaseScore) -> str:
    if case.wer is None:
        return "N/A"
    return f"{case.wer:.4f} (`{case.word_tokenizer}`)"


def _aggregate_wer(aggregate: AggregateScore) -> str:
    value = "N/A" if aggregate.mean_wer is None else f"{aggregate.mean_wer:.4f}"
    return f"{value} ({aggregate.wer_case_count} / {aggregate.case_count})"


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
    corpus_sha256: str | None = None,
    ground_truth_review_status: str | None = None,
    ground_truth_note: str | None = None,
) -> str:
    """Render one Markdown report for every scored variant."""

    review_status = ground_truth_review_status or "not declared"
    lines = [
        "# Benchmark Report",
        "",
        f"- corpus: `{corpus}`",
        f"- manifest sha256: `{manifest_sha256}`",
    ]
    if corpus_sha256 is not None:
        lines.append(f"- corpus content sha256: `{corpus_sha256}`")
    lines += [
        f"- generated: {generated_at}",
        f"- ground-truth review: `{review_status}`",
        f"- variants: {len(reports)}",
    ]
    if ground_truth_note is not None and ground_truth_note.strip():
        note = " ".join(ground_truth_note.split())
        lines += [f"> Ground-truth note: {note}", ""]
    else:
        lines.append("")
    if not reports:
        lines += ["No variants were scored.", ""]
        return "\n".join(lines)

    lines += [
        "## Metric semantics",
        "",
        (
            "- CER is the primary Chinese/Japanese accuracy metric. Chinese, Japanese "
            "and unknown-language cases report WER as `N/A`; no implicit segmenter is used."
        ),
        (
            "- Applicable WER uses a declared tokenizer. `whitespace-v1` preserves case "
            "and punctuation and splits only on Unicode whitespace; WER from other "
            "tokenizers is not directly comparable."
        ),
        (
            "- Mean WER is a macro average over applicable cases only; `(n / N)` shows "
            "the applicable/total case counts. WER can exceed 1.0 when edit insertions "
            "outnumber reference tokens."
        ),
        (
            "- LCS Missing/Extra are strict whole-line, ordered differences. Changed uses "
            "one-to-one `SequenceMatcher.ratio >= 0.5` text matching among those unmatched "
            "lines; it is not geometric or physical-region alignment. Net M/E subtracts "
            "Changed from the corresponding strict LCS count and is not proof of layout loss."
        ),
        "",
        "## Variants",
        "",
        (
            "| Variant | Cases | Mean CER | Mean WER (applicable / total) | Punctuation "
            "| LCS Missing | LCS Extra | Changed | Net Missing | Net Extra "
            "| Reading-order errors "
            "| Accepted pairs | Unresolved pairs | Elapsed s | Pages/min |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        aggregate = report.aggregate
        lines.append(
            f"| {report.engine} | {aggregate.case_count} | "
            f"{aggregate.mean_cer:.4f} | {_aggregate_wer(aggregate)} | "
            f"{aggregate.mean_punctuation_accuracy:.4f} | "
            f"{aggregate.missing_lines} | {aggregate.extra_lines} | "
            f"{aggregate.changed_lines} | {aggregate.net_missing_lines} | "
            f"{aggregate.net_extra_lines} | "
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
                "| Case | Language | Tags | CER | WER (tokenizer) | Punctuation "
                "| LCS Missing | LCS Extra | Changed | Net Missing | Net Extra "
                "| Reading-order errors | Accepted pairs | Unresolved pairs | Seconds |"
            ),
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for case in report.cases:
            tags = ",".join(case.tags) if case.tags else "-"
            lines.append(
                f"| {case.case_id} | {case.language} | {tags} | {case.cer:.4f} | "
                f"{_case_wer(case)} | {case.punctuation_accuracy:.4f} | "
                f"{case.missing_lines} | {case.extra_lines} | {case.changed_lines} | "
                f"{case.net_missing_lines} | {case.net_extra_lines} | "
                f"{case.reading_order_errors} | "
                f"{case.accepted_pairs if case.accepted_pairs is not None else '-'} | "
                f"{case.unresolved_pairs if case.unresolved_pairs is not None else '-'} | "
                f"{case.elapsed_seconds:.3f} |"
            )
        lines.append("")
    return "\n".join(lines)
