"""Deterministic Markdown report for benchmark runs.

The report keeps the aggregate table for comparison and one table per variant for
every case, so an improvement on easy pages cannot hide a regression on a
difficult page class. Timestamps are injected instead of read from the clock, so
two runs over the same results render identically.
"""

from __future__ import annotations

from collections.abc import Sequence

from scribeforge.benchmark.metrics import (
    mean_confidence_interval,
    wilson_score_interval,
    z_score_for_confidence,
)
from scribeforge.benchmark.runner import (
    AggregateScore,
    BenchmarkReport,
    CaseScore,
    aggregate_case_scores,
)


def _direction_of(case: CaseScore) -> str:
    """Return the case's declared direction, or ``horizontal-lr`` when unstated.

    A corpus that predates the direction field is horizontal: that is the only
    thing the existing six pages are, and silently grouping them under "unknown"
    would hide the very split the vertical gate needs to see.
    """

    return case.direction or "horizontal-lr"


def _mean(values: Sequence[float], empty: float) -> float:
    return sum(values) / len(values) if values else empty


def _interval_text(interval: tuple[float, float] | None) -> str:
    if interval is None:
        return ""
    return f" [{interval[0]:.4f}, {interval[1]:.4f}]"


def _macro_rate(
    rate: float | None, values: Sequence[float], *, confidence: float
) -> str:
    if rate is None:
        return "N/A"
    return f"{rate:.4f}{_interval_text(mean_confidence_interval(values, confidence=confidence))}"


def _case_cer(case: CaseScore, *, confidence: float) -> str:
    value = f"{case.cer:.4f}"
    if case.character_errors is not None and case.reference_characters is not None:
        value += f" ({case.character_errors} / {case.reference_characters} chars)"
        value += _interval_text(
            wilson_score_interval(
                case.character_errors, case.reference_characters, confidence=confidence
            )
        )
    return value


def _case_wer(case: CaseScore, *, confidence: float) -> str:
    if case.wer is None:
        return "N/A"
    if case.word_errors is not None and case.reference_words is not None:
        counts = f"{case.word_errors} / {case.reference_words} words, "
        interval = _interval_text(
            wilson_score_interval(
                case.word_errors, case.reference_words, confidence=confidence
            )
        )
    else:
        counts = ""
        interval = ""
    return f"{case.wer:.4f} ({counts}`{case.word_tokenizer}`){interval}"


def _micro_rate(
    rate: float | None,
    errors: int | None,
    reference_units: int | None,
    *,
    confidence: float,
) -> str:
    if rate is None or errors is None or reference_units is None:
        return "N/A"
    interval = _interval_text(
        wilson_score_interval(errors, reference_units, confidence=confidence)
    )
    return f"{rate:.4f} ({errors} / {reference_units}){interval}"


def _micro_cer(aggregate: AggregateScore, *, confidence: float) -> str:
    return _micro_rate(
        aggregate.micro_cer,
        aggregate.character_errors,
        aggregate.reference_characters,
        confidence=confidence,
    )


def _micro_wer(aggregate: AggregateScore, *, confidence: float) -> str:
    return _micro_rate(
        aggregate.micro_wer,
        aggregate.word_errors,
        aggregate.reference_words,
        confidence=confidence,
    )


def _aggregate_wer(
    aggregate: AggregateScore, cases: Sequence[CaseScore], *, confidence: float
) -> str:
    applicable = [case.wer for case in cases if case.wer is not None]
    value = _macro_rate(aggregate.mean_wer, applicable, confidence=confidence)
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


def _tag_rows(reports: Sequence[BenchmarkReport], *, confidence: float) -> list[str]:
    """Aggregate declared manifest tags without treating them as disjoint."""

    ci = f"{confidence:.0%} CI"
    tags = sorted({tag for report in reports for case in report.cases for tag in case.tags})
    include_untagged = any(not case.tags for report in reports for case in report.cases)
    if not tags and not include_untagged:
        return []
    labels = [*tags]
    if include_untagged:
        labels.append("(untagged)")
    lines = [
        "## Results by manifest tag",
        "",
        (
            f"| Variant | Tag | Cases | Macro CER ({ci}) | Micro CER (edits / chars, {ci}) "
            f"| Macro WER (applicable / total, {ci}) | Micro WER (edits / words, {ci}) |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        for label in labels:
            if label == "(untagged)":
                cases = tuple(case for case in report.cases if not case.tags)
            else:
                cases = tuple(case for case in report.cases if label in case.tags)
            if not cases:
                continue
            aggregate = aggregate_case_scores(cases)
            lines.append(
                f"| {report.engine} | {label} | {len(cases)} | "
                f"{_macro_rate(aggregate.mean_cer, [case.cer for case in cases], confidence=confidence)} | "
                f"{_micro_cer(aggregate, confidence=confidence)} | "
                f"{_aggregate_wer(aggregate, cases, confidence=confidence)} | "
                f"{_micro_wer(aggregate, confidence=confidence)} |"
            )
    lines += [
        "",
        (
            "> Tags are declared manifest labels, not inferred layout classes; multi-tag "
            "cases appear once in each applicable row, so rows are not disjoint. "
            "`(untagged)` makes cases without labels visible."
        ),
        "",
    ]
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
    confidence: float = 0.95,
) -> str:
    """Render one Markdown report for every scored variant.

    ``confidence`` selects the two-sided confidence level (90%, 95% or 99%) for
    the deterministic intervals shown next to every macro and micro rate.
    """

    z_score_for_confidence(confidence)  # fail fast on an unsupported level
    ci = f"{confidence:.0%} CI"
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
            "- Macro CER weights every case equally; macro WER weights applicable "
            "cases equally and shows `(applicable / total)` case counts. Micro rates "
            "are `sum(edits) / sum(reference units)` and show those raw counts. Micro "
            "WER is N/A without an applicable case; either micro rate is N/A when "
            "its required counts are incomplete. WER can exceed 1.0 through insertions."
        ),
        (
            f"- Every macro and micro rate carries a deterministic {ci} rendered as "
            "`[lower, upper]`. Per-case and micro rates use the Wilson score interval "
            f"over their raw edit counts at the {confidence:.0%} level; macro rates use "
            "the normal approximation over per-case rates and need at least two cases "
            "(fewer cases render the bare mean). Intervals for rates above 1.0, which "
            "WER can reach through insertions, are approximate."
        ),
        (
            "- LCS Missing/Extra are strict whole-line, ordered differences. Changed uses "
            "one-to-one `SequenceMatcher.ratio >= 0.5` text matching among those unmatched "
            "lines; it is not geometric or physical-region alignment. Net M/E subtracts "
            "Changed from the corresponding strict LCS count and is not proof of layout loss."
        ),
        (
            "- Spatial Missing/Extra are geometry-only: greedy one-to-one box matching "
            "(IoU >= 0.5) between declared reference line boxes and the engine's reported "
            "line boxes. They are shown only when reference geometry is declared (for "
            "example in oracle-ceiling runs) and render as `-` otherwise; a box match does "
            "not prove the text matched, and strict LCS Missing/Extra remain the "
            "text-based counts. A one-character line error is Changed, not a spatial "
            "missing line."
        ),
        "",
        "## Variants",
        "",
        (
            f"| Variant | Cases | Macro CER ({ci}) | Micro CER (edits / chars, {ci}) "
            f"| Macro WER (applicable / total, {ci}) | Micro WER (edits / words, {ci}) | Punctuation "
            "| LCS Missing | LCS Extra | Changed | Net Missing | Net Extra "
            "| Spatial Missing | Spatial Extra | Reading-order errors "
            "| Accepted pairs | Unresolved pairs | Elapsed s | Pages/min |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        aggregate = report.aggregate
        lines.append(
            f"| {report.engine} | {aggregate.case_count} | "
            f"{_macro_rate(aggregate.mean_cer, [case.cer for case in report.cases], confidence=confidence)} | "
            f"{_micro_cer(aggregate, confidence=confidence)} | "
            f"{_aggregate_wer(aggregate, report.cases, confidence=confidence)} | "
            f"{_micro_wer(aggregate, confidence=confidence)} | "
            f"{aggregate.mean_punctuation_accuracy:.4f} | "
            f"{aggregate.missing_lines} | {aggregate.extra_lines} | "
            f"{aggregate.changed_lines} | {aggregate.net_missing_lines} | "
            f"{aggregate.net_extra_lines} | "
            f"{aggregate.spatial_missing_lines if aggregate.spatial_missing_lines is not None else '-'} | "
            f"{aggregate.spatial_extra_lines if aggregate.spatial_extra_lines is not None else '-'} | "
            f"{aggregate.reading_order_errors} | "
            f"{aggregate.accepted_pairs if aggregate.accepted_pairs is not None else '-'} | "
            f"{aggregate.unresolved_pairs if aggregate.unresolved_pairs is not None else '-'} | "
            f"{aggregate.elapsed_seconds:.3f} | {aggregate.pages_per_minute:.2f} |"
        )

    direction_rows = _direction_rows(reports)
    if direction_rows:
        lines += [""] + direction_rows
    tag_rows = _tag_rows(reports, confidence=confidence)
    if tag_rows:
        lines += [""] + tag_rows

    lines += ["", "## Per-case results", ""]
    for report in reports:
        lines += [
            f"### {report.engine}",
            "",
            (
                "| Case | Language | Tags | CER | WER (tokenizer) | Punctuation "
                "| LCS Missing | LCS Extra | Changed | Net Missing | Net Extra "
                "| Spatial Missing | Spatial Extra | Reading-order errors "
                "| Accepted pairs | Unresolved pairs | Seconds |"
            ),
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for case in report.cases:
            tags = ",".join(case.tags) if case.tags else "-"
            lines.append(
                f"| {case.case_id} | {case.language} | {tags} | {_case_cer(case, confidence=confidence)} | "
                f"{_case_wer(case, confidence=confidence)} | {case.punctuation_accuracy:.4f} | "
                f"{case.missing_lines} | {case.extra_lines} | {case.changed_lines} | "
                f"{case.net_missing_lines} | {case.net_extra_lines} | "
                f"{case.spatial_missing_lines if case.spatial_missing_lines is not None else '-'} | "
                f"{case.spatial_extra_lines if case.spatial_extra_lines is not None else '-'} | "
                f"{case.reading_order_errors} | "
                f"{case.accepted_pairs if case.accepted_pairs is not None else '-'} | "
                f"{case.unresolved_pairs if case.unresolved_pairs is not None else '-'} | "
                f"{case.elapsed_seconds:.3f} |"
            )
        lines.append("")
    return "\n".join(lines)
