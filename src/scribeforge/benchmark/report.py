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
from scribeforge.benchmark.quality_gate import coverage_warnings
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


def _macro_rate(rate: float | None, values: Sequence[float], *, confidence: float) -> str:
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
        # Show the tokenizer label even for N/A so every row declares WER
        # applicability instead of leaving readers to guess why it is missing.
        return f"N/A (`{case.word_tokenizer}`)"
    if case.word_errors is not None and case.reference_words is not None:
        counts = f"{case.word_errors} / {case.reference_words} words, "
        interval = _interval_text(
            wilson_score_interval(case.word_errors, case.reference_words, confidence=confidence)
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
    interval = _interval_text(wilson_score_interval(errors, reference_units, confidence=confidence))
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
    """Aggregate CER and reading-order error rate per writing direction.

    Rendered whenever at least one case *declares* a direction, so a corpus
    that declares vertical pages always gets its vertical row — even when
    every case is vertical. Corpora that declare nothing keep the old
    behavior of no direction table.
    """

    declared = any(case.direction is not None for report in reports for case in report.cases)
    if not declared:
        return []
    directions = sorted({_direction_of(case) for report in reports for case in report.cases})
    lines = [
        "## Results by writing direction",
        "",
        (
            "| Variant | Direction | Cases | Mean CER | Unresolved pairs "
            "| Reading-order errors | RO err rate |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
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
                f"{sum(case.reading_order_errors for case in cases)} | "
                f"{_mean([case.reading_order_error_rate for case in cases], 0.0):.4f} |"
            )
    lines.append("")
    return lines


def _vertical_rows(reports: Sequence[BenchmarkReport]) -> list[str]:
    """Dedicated vertical-text reading-order section (#68).

    Reading order is the core metric for vertical writing: a vertically
    perfect OCR that reads columns left-to-right still fails the page. The
    section appears whenever any case declares a vertical direction, and only
    vertical cases feed it.
    """

    any_vertical = any(
        (case.direction or "").startswith("vertical") for report in reports for case in report.cases
    )
    if not any_vertical:
        return []
    lines = [
        "## Vertical text reading order",
        "",
        "| Variant | Vertical cases | Mean CER | Reading-order errors | RO err rate |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        cases = [case for case in report.cases if (case.direction or "").startswith("vertical")]
        if not cases:
            continue
        lines.append(
            f"| {report.engine} | {len(cases)} | "
            f"{_mean([case.cer for case in cases], 0.0):.4f} | "
            f"{sum(case.reading_order_errors for case in cases)} | "
            f"{_mean([case.reading_order_error_rate for case in cases], 0.0):.4f} |"
        )
    lines += [
        "",
        (
            "> RO err rate is reading-order inversions per reference line, recorded "
            "separately for vertical pages because column order is the core "
            "vertical-layout failure mode."
        ),
        "",
    ]
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


def _language_rows(reports: Sequence[BenchmarkReport], *, confidence: float) -> list[str]:
    """Aggregate the same metrics per declared case language.

    Languages are manifest metadata, not inferred classes. A language that has
    no corpus case simply has no row, so a claim about an unsupported language
    stays visible as an absence instead of being averaged away.
    """

    ci = f"{confidence:.0%} CI"
    languages = sorted({case.language for report in reports for case in report.cases})
    if not languages:
        return []
    lines = [
        "## Results by language",
        "",
        (
            f"| Variant | Language | Cases | Macro CER ({ci}) | Micro CER (edits / chars, {ci}) "
            f"| Macro WER (applicable / total, {ci}) | Micro WER (edits / words, {ci}) "
            f"| Reading-order errors | RO err rate |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in reports:
        for language in languages:
            cases = tuple(case for case in report.cases if case.language == language)
            if not cases:
                continue
            aggregate = aggregate_case_scores(cases)
            lines.append(
                f"| {report.engine} | {language} | {len(cases)} | "
                f"{_macro_rate(aggregate.mean_cer, [case.cer for case in cases], confidence=confidence)} | "
                f"{_micro_cer(aggregate, confidence=confidence)} | "
                f"{_aggregate_wer(aggregate, cases, confidence=confidence)} | "
                f"{_micro_wer(aggregate, confidence=confidence)} | "
                f"{aggregate.reading_order_errors} | {aggregate.reading_order_error_rate:.4f} |"
            )
    lines += [
        "",
        (
            "> Per-language grouping is mandatory: a language without corpus cases has no "
            "row, so unsupported-language claims remain visible as an absence rather than "
            "being averaged away. RO err rate is reading-order inversions per reference line."
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
    manifest_license: str | None = None,
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
    language_counts: dict[str, int] = {}
    for report in reports:
        for case in report.cases:
            language_counts[case.language] = language_counts.get(case.language, 0) + 1
    languages_summary = ", ".join(
        f"`{language}` ({count})" for language, count in sorted(language_counts.items())
    )
    lines += [
        f"- generated: {generated_at}",
        f"- ground-truth review: `{review_status}`",
        f"- corpus license: `{manifest_license}`"
        if manifest_license
        else "- corpus license: not declared",
        f"- variants: {len(reports)}",
    ]
    if languages_summary:
        lines.append(f"- declared languages: {languages_summary}")
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
            "and unknown-language cases report WER as `N/A` with tokenizer "
            "`not-applicable`; no implicit segmenter is used."
        ),
        (
            "- Chinese WER above 1.0 has no recognition meaning: with whitespace "
            '"words", an unsegmented Chinese line is one giant token, so a single '
            'inserted line can add more edits than the reference "words". That is a '
            "tokenizer artifact, not recognition quality, which is why those cases "
            "report N/A. Wherever WER is applicable, a value above 1.0 simply means "
            "insertions outnumber reference words."
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
            "| Accepted pairs | Unresolved pairs | False-accepted pairs | Elapsed s | Pages/min |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---:  | ---: |",
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
            f"{aggregate.reading_order_errors} | {aggregate.reading_order_error_rate:.4f} | "
            f"{aggregate.accepted_pairs if aggregate.accepted_pairs is not None else '-'} | "
            f"{aggregate.unresolved_pairs if aggregate.unresolved_pairs is not None else '-'} | "
            f"{aggregate.false_accepted_pairs if aggregate.false_accepted_pairs is not None else '-'} | "
            f"{aggregate.elapsed_seconds:.3f} | {aggregate.pages_per_minute:.2f} |"
        )

    direction_rows = _direction_rows(reports)
    if direction_rows:
        lines += [""] + direction_rows
    vertical_rows = _vertical_rows(reports)
    if vertical_rows:
        lines += [""] + vertical_rows
    tag_rows = _tag_rows(reports, confidence=confidence)
    if tag_rows:
        lines += [""] + tag_rows
    language_rows = _language_rows(reports, confidence=confidence)
    if language_rows:
        lines += [""] + language_rows

    all_cases = [case for report in reports for case in report.cases]
    warnings = coverage_warnings(all_cases)
    if warnings:
        lines += ["", "## Corpus coverage warnings", ""]
        lines += [f"- {warning}" for warning in warnings]
        lines.append("")

    lines += ["", "## Per-case results", ""]
    for report in reports:
        lines += [
            f"### {report.engine}",
            "",
            (
                "| Case | Language | Tags | CER | WER (tokenizer) | Punctuation "
                "| LCS Missing | LCS Extra | Changed | Net Missing | Net Extra "
                "| Spatial Missing | Spatial Extra | Reading-order errors | RO err rate "
                "| Accepted pairs | Unresolved pairs | False-accepted pairs | Seconds |"
            ),
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
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
                f"{case.reading_order_errors} | {case.reading_order_error_rate:.4f} | "
                f"{case.accepted_pairs if case.accepted_pairs is not None else '-'} | "
                f"{case.unresolved_pairs if case.unresolved_pairs is not None else '-'} | "
                f"{case.false_accepted_pairs if case.false_accepted_pairs is not None else '-'} | "
                f"{case.elapsed_seconds:.3f} |"
            )
        lines.append("")
    return "\n".join(lines)


def _case_json(case: CaseScore) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "language": case.language,
        "tags": list(case.tags),
        "direction": case.direction,
        "cer": case.cer,
        "wer": case.wer,
        "word_tokenizer": case.word_tokenizer,
        "punctuation_accuracy": case.punctuation_accuracy,
        "missing_lines": case.missing_lines,
        "extra_lines": case.extra_lines,
        "changed_lines": case.changed_lines,
        "net_missing_lines": case.net_missing_lines,
        "net_extra_lines": case.net_extra_lines,
        "reading_order_errors": case.reading_order_errors,
        "reading_order_error_rate": case.reading_order_error_rate,
        "spatial_missing_lines": case.spatial_missing_lines,
        "spatial_extra_lines": case.spatial_extra_lines,
        "accepted_pairs": case.accepted_pairs,
        "unresolved_pairs": case.unresolved_pairs,
        "false_accepted_pairs": case.false_accepted_pairs,
        "character_errors": case.character_errors,
        "reference_characters": case.reference_characters,
        "word_errors": case.word_errors,
        "reference_words": case.reference_words,
        "elapsed_seconds": case.elapsed_seconds,
    }


def _aggregate_json(aggregate: AggregateScore) -> dict[str, object]:
    return {
        "case_count": aggregate.case_count,
        "mean_cer": aggregate.mean_cer,
        "micro_cer": aggregate.micro_cer,
        "mean_wer": aggregate.mean_wer,
        "micro_wer": aggregate.micro_wer,
        "wer_case_count": aggregate.wer_case_count,
        "mean_punctuation_accuracy": aggregate.mean_punctuation_accuracy,
        "missing_lines": aggregate.missing_lines,
        "extra_lines": aggregate.extra_lines,
        "changed_lines": aggregate.changed_lines,
        "net_missing_lines": aggregate.net_missing_lines,
        "net_extra_lines": aggregate.net_extra_lines,
        "reading_order_errors": aggregate.reading_order_errors,
        "reading_order_error_rate": aggregate.reading_order_error_rate,
        "spatial_missing_lines": aggregate.spatial_missing_lines,
        "spatial_extra_lines": aggregate.spatial_extra_lines,
        "accepted_pairs": aggregate.accepted_pairs,
        "unresolved_pairs": aggregate.unresolved_pairs,
        "false_accepted_pairs": aggregate.false_accepted_pairs,
        "character_errors": aggregate.character_errors,
        "reference_characters": aggregate.reference_characters,
        "word_errors": aggregate.word_errors,
        "reference_words": aggregate.reference_words,
        "elapsed_seconds": aggregate.elapsed_seconds,
        "pages_per_minute": aggregate.pages_per_minute,
    }


def render_report_json(
    reports: Sequence[BenchmarkReport],
    *,
    corpus: str,
    manifest_sha256: str,
    generated_at: str,
    corpus_sha256: str | None = None,
    ground_truth_review_status: str | None = None,
    ground_truth_note: str | None = None,
    manifest_license: str | None = None,
    confidence: float = 0.95,
) -> str:
    """Render the same run as deterministic schema-v1 JSON.

    The per-language strata are mandatory: ``by_language`` always exists so a
    consumer can detect an unverified language as an absence. ``by_direction``
    and ``vertical_reading_order`` are empty lists when no case declares a
    direction / vertical direction, and every rate field uses the same
    zero-reference conventions as the Markdown report.
    """

    import json

    z_score_for_confidence(confidence)  # fail fast on an unsupported level
    language_counts: dict[str, int] = {}
    for report in reports:
        for case in report.cases:
            language_counts[case.language] = language_counts.get(case.language, 0) + 1

    variants: list[dict[str, object]] = []
    for report in reports:
        by_language: list[dict[str, object]] = []
        languages = sorted({case.language for case in report.cases})
        for language in languages:
            cases = tuple(case for case in report.cases if case.language == language)
            if not cases:
                continue
            aggregate = aggregate_case_scores(cases)
            by_language.append(
                {
                    "language": language,
                    "cases": aggregate.case_count,
                    "mean_cer": aggregate.mean_cer,
                    "micro_cer": aggregate.micro_cer,
                    "mean_wer": aggregate.mean_wer,
                    "micro_wer": aggregate.micro_wer,
                    "wer_case_count": aggregate.wer_case_count,
                    "reading_order_errors": aggregate.reading_order_errors,
                    "reading_order_error_rate": aggregate.reading_order_error_rate,
                }
            )
        declared = any(case.direction is not None for case in report.cases)
        by_direction: list[dict[str, object]] = []
        if declared:
            for direction in sorted({_direction_of(case) for case in report.cases}):
                direction_cases = [
                    case for case in report.cases if _direction_of(case) == direction
                ]
                if not direction_cases:
                    continue
                by_direction.append(
                    {
                        "direction": direction,
                        "cases": len(direction_cases),
                        "mean_cer": _mean([case.cer for case in direction_cases], 0.0),
                        "unresolved_pairs": sum(case.unresolved_pairs or 0 for case in cases),
                        "reading_order_errors": sum(case.reading_order_errors for case in cases),
                        "reading_order_error_rate": _mean(
                            [case.reading_order_error_rate for case in direction_cases], 0.0
                        ),
                    }
                )
        vertical_cases = [
            case for case in report.cases if (case.direction or "").startswith("vertical")
        ]
        vertical_reading_order: list[dict[str, object]] = []
        if vertical_cases:
            vertical_reading_order.append(
                {
                    "cases": len(vertical_cases),
                    "mean_cer": _mean([case.cer for case in vertical_cases], 0.0),
                    "reading_order_errors": sum(
                        case.reading_order_errors for case in vertical_cases
                    ),
                    "reading_order_error_rate": _mean(
                        [case.reading_order_error_rate for case in vertical_cases], 0.0
                    ),
                }
            )
        variants.append(
            {
                "engine": report.engine,
                "aggregate": _aggregate_json(report.aggregate),
                "by_language": by_language,
                "by_direction": by_direction,
                "vertical_reading_order": vertical_reading_order,
                "cases": [_case_json(case) for case in report.cases],
            }
        )

    payload: dict[str, object] = {
        "schema_version": 1,
        "corpus": corpus,
        "manifest_sha256": manifest_sha256,
        "generated_at": generated_at,
        "confidence": confidence,
        "ground_truth_review_status": ground_truth_review_status or "not declared",
        "manifest_license": manifest_license,
        "declared_languages": dict(sorted(language_counts.items())),
        "variants": variants,
    }
    if corpus_sha256 is not None:
        payload["corpus_sha256"] = corpus_sha256
    if ground_truth_note is not None and ground_truth_note.strip():
        payload["ground_truth_note"] = " ".join(ground_truth_note.split())
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
