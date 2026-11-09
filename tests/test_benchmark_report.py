from __future__ import annotations

from dataclasses import replace

from scribeforge.benchmark.report import render_report
from scribeforge.benchmark.runner import (
    AggregateScore,
    BenchmarkReport,
    CaseScore,
    aggregate_case_scores,
)


def _case(
    case_id: str = "case-1",
    *,
    cer: float = 0.25,
    tags: tuple[str, ...] = ("body", "punctuation"),
    elapsed: float = 2.0,
) -> CaseScore:
    return CaseScore(
        case_id=case_id,
        language="en",
        tags=tags,
        cer=cer,
        wer=0.5,
        punctuation_accuracy=0.75,
        missing_lines=0,
        extra_lines=1,
        reading_order_errors=2,
        elapsed_seconds=elapsed,
    )


def _report(
    engine: str, *, cer: float = 0.25, elapsed: float = 2.0, pages_per_minute: float = 30.0
) -> BenchmarkReport:
    case = _case(cer=cer, elapsed=elapsed)
    return BenchmarkReport(
        engine=engine,
        cases=(case,),
        aggregate=AggregateScore(
            case_count=1,
            mean_cer=cer,
            mean_wer=0.5,
            mean_punctuation_accuracy=0.75,
            missing_lines=0,
            extra_lines=1,
            reading_order_errors=2,
            elapsed_seconds=elapsed,
            pages_per_minute=pages_per_minute,
            wer_case_count=1,
        ),
    )


def test_render_report_summarizes_variants_and_per_case_results() -> None:
    text = render_report(
        (_report("mineru"), _report("paddleocr", cer=0.5, elapsed=1.0, pages_per_minute=60.0)),
        corpus="corpus/manifest.json",
        manifest_sha256="abc123",
        generated_at="2026-10-20T00:00:00Z",
    )

    lines = text.splitlines()
    assert lines[0] == "# Benchmark Report"
    assert "- corpus: `corpus/manifest.json`" in lines
    assert "- manifest sha256: `abc123`" in lines
    assert "- generated: 2026-10-20T00:00:00Z" in lines
    assert "- variants: 2" in lines
    assert (
        "| mineru | 1 | 0.2500 | N/A | 0.5000 (1 / 1) | N/A | 0.7500 | 0 | 1 | 0 | 0 | 1 | - | - | 2 | - | - | 2.000 | 30.00 |"
        in lines
    )
    assert (
        "| paddleocr | 1 | 0.5000 | N/A | 0.5000 (1 / 1) | N/A | 0.7500 | 0 | 1 | 0 | 0 | 1 | - | - | 2 | - | - | 1.000 | 60.00 |"
        in lines
    )
    assert (
        "| case-1 | en | body,punctuation | 0.2500 | 0.5000 (`whitespace-v1`) | "
        "0.7500 | 0 | 1 | 0 | 0 | 1 | - | - | 2 | - | - | 2.000 |" in lines
    )
    assert text.index("| mineru |") < text.index("| paddleocr |")
    assert text.count("### mineru") == 1
    assert text.count("### paddleocr") == 1


def test_render_report_states_when_no_variant_was_scored() -> None:
    text = render_report(
        (), corpus="corpus/manifest.json", manifest_sha256="abc123", generated_at="t"
    )

    assert "No variants were scored." in text.splitlines()


def test_render_report_discloses_ground_truth_review_status_and_note() -> None:
    text = render_report(
        (_report("oracle"),),
        corpus="corpus/manifest.json",
        manifest_sha256="abc123",
        corpus_sha256="def456",
        generated_at="t",
        ground_truth_review_status="single-pass",
        ground_truth_note="Single transcription; not independently proofread.",
    )

    assert "- ground-truth review: `single-pass`" in text.splitlines()
    assert "- corpus content sha256: `def456`" in text.splitlines()
    assert "> Ground-truth note: Single transcription; not independently proofread." in text


def test_render_report_does_not_infer_ground_truth_review_status() -> None:
    text = render_report((_report("oracle"),), corpus="c", manifest_sha256="h", generated_at="t")

    assert "- ground-truth review: `not declared`" in text.splitlines()


def test_render_report_marks_a_case_without_tags() -> None:
    report = _report("mineru")
    untagged = BenchmarkReport(
        engine="mineru",
        cases=(
            CaseScore(
                case_id="case-1",
                language="en",
                tags=(),
                cer=0.0,
                wer=0.0,
                punctuation_accuracy=1.0,
                missing_lines=0,
                extra_lines=0,
                reading_order_errors=0,
                elapsed_seconds=0.5,
            ),
        ),
        aggregate=report.aggregate,
    )

    text = render_report((untagged,), corpus="m.json", manifest_sha256="abc123", generated_at="t")

    assert "| case-1 | en | - | 0.0000 |" in text


def _mixed_report() -> BenchmarkReport:
    horizontal = _case("h-1", cer=0.10, tags=("body",))
    vertical = CaseScore(
        case_id="v-1",
        language="en",
        tags=("body", "vertical"),
        cer=0.40,
        wer=0.5,
        punctuation_accuracy=0.75,
        missing_lines=0,
        extra_lines=0,
        reading_order_errors=3,
        elapsed_seconds=1.0,
        direction="vertical-rl",
    )
    return BenchmarkReport(
        engine="mixed",
        cases=(horizontal, vertical),
        aggregate=AggregateScore(
            case_count=2,
            mean_cer=0.25,
            mean_wer=0.5,
            mean_punctuation_accuracy=0.75,
            missing_lines=0,
            extra_lines=1,
            reading_order_errors=5,
            elapsed_seconds=3.0,
            pages_per_minute=40.0,
            wer_case_count=2,
        ),
    )


def test_report_splits_cer_by_writing_direction() -> None:
    """R5 requires horizontal and vertical CER to be readable separately.

    Averaging a vertical failure into a horizontal average would hide exactly the
    regression the ancient-book gate is looking for.
    """

    text = render_report(
        [_mixed_report()],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "Results by writing direction" in text
    assert "| mixed | horizontal-lr | 1 | 0.1000 |" in text
    assert "| mixed | vertical-rl | 1 | 0.4000 |" in text


def test_report_omits_the_direction_table_for_a_single_direction() -> None:
    text = render_report([_report("mineru")], corpus="c", manifest_sha256="h", generated_at="now")

    assert "Results by writing direction" not in text


def test_wer_aggregation_and_report_exclude_not_applicable_cases() -> None:
    english = replace(
        _case(case_id="english"),
        wer=0.5,
        missing_lines=1,
        extra_lines=1,
        changed_lines=1,
        word_tokenizer="whitespace-v1",
    )
    chinese = replace(
        _case(case_id="chinese"),
        language="zh-Hant",
        wer=None,
        word_tokenizer="not-applicable",
    )
    aggregate = aggregate_case_scores((english, chinese))

    assert aggregate.mean_wer == 0.5
    assert aggregate.wer_case_count == 1
    assert aggregate.changed_lines == 1

    text = render_report(
        (BenchmarkReport(engine="ocr", cases=(english, chinese), aggregate=aggregate),),
        corpus="corpus/manifest.json",
        manifest_sha256="abc123",
        generated_at="2026-10-20T00:00:00Z",
    )

    assert "| ocr | 2 | 0.2500 [0.2500, 0.2500] | N/A | 0.5000 (1 / 2) | N/A |" in text
    assert "| english | en | body,punctuation | 0.2500 | 0.5000 (`whitespace-v1`) |" in text
    assert "| chinese | zh-Hant | body,punctuation | 0.2500 | N/A (`not-applicable`) |" in text
    assert "| 0.7500 | 1 | 1 | 1 | 0 | 0 | - | - | 2 |" in text
    assert "`whitespace-v1` preserves case and punctuation" in text
    assert "WER can exceed 1.0" in text
    assert "Changed uses one-to-one `SequenceMatcher.ratio >= 0.5` text matching" in text
    assert "not geometric or physical-region alignment" in text


def test_report_explains_what_chinese_wer_above_one_means() -> None:
    """#80 acceptance: the report must explain Chinese WER > 1, not just show it."""

    chinese = replace(
        _case(case_id="chinese"),
        language="zh-Hant",
        wer=None,
        word_tokenizer="not-applicable",
    )
    report = BenchmarkReport(
        engine="ocr",
        cases=(chinese,),
        aggregate=aggregate_case_scores((chinese,)),
    )
    text = render_report((report,), corpus="c", manifest_sha256="h", generated_at="now")

    # The semantics section states why whitespace WER on Chinese would exceed
    # 1.0 (one giant token per line) and that this is a tokenizer artifact, so
    # Chinese WER is N/A and CER is primary.
    assert "tokenizer artifact" in text
    assert "one giant token" in text
    assert "CER is the primary" in text
    # Every per-case row declares WER applicability via the tokenizer label.
    assert "| chinese | zh-Hant | body,punctuation | 0.2500 | N/A (`not-applicable`) |" in text


def test_all_not_applicable_wer_still_discloses_zero_applicable_cases() -> None:
    empty = aggregate_case_scores(())
    assert empty.mean_wer is None
    assert (empty.wer_case_count, empty.case_count) == (0, 0)

    chinese = replace(
        _case(case_id="chinese"),
        language="zh-Hant",
        wer=None,
        word_tokenizer="not-applicable",
    )
    report = BenchmarkReport(
        engine="ocr",
        cases=(chinese,),
        aggregate=aggregate_case_scores((chinese,)),
    )

    text = render_report((report,), corpus="c", manifest_sha256="h", generated_at="now")

    assert report.aggregate.mean_wer is None
    assert "| ocr | 1 | 0.2500 | N/A | N/A (0 / 1) | N/A |" in text


def test_micro_rates_use_raw_edit_counts_and_manifest_tags_are_stratified() -> None:
    small = replace(
        _case(case_id="small", cer=0.5, tags=("body", "sparse")),
        wer=1.0,
        character_errors=1,
        reference_characters=2,
        word_errors=1,
        reference_words=1,
    )
    large = replace(
        _case(case_id="large", cer=0.01, tags=("body", "dense")),
        wer=0.01,
        character_errors=1,
        reference_characters=100,
        word_errors=1,
        reference_words=100,
    )
    aggregate = aggregate_case_scores((small, large))

    assert aggregate.mean_cer == 0.255
    assert aggregate.micro_cer == 2 / 102
    assert aggregate.mean_wer == 0.505
    assert aggregate.micro_wer == 2 / 101
    assert (aggregate.character_errors, aggregate.reference_characters) == (2, 102)
    assert (aggregate.word_errors, aggregate.reference_words) == (2, 101)

    text = render_report(
        (BenchmarkReport(engine="ocr", cases=(small, large), aggregate=aggregate),),
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "## Results by manifest tag" in text
    assert (
        "| ocr | body | 2 | 0.2550 [0.0000, 0.7352] | 0.0196 (2 / 102) [0.0054, 0.0687] | "
        "0.5050 [0.0000, 1.4752] (2 / 2) | 0.0198 (2 / 101) [0.0054, 0.0693] |" in text
    )
    assert "| ocr | sparse | 1 | 0.5000 | 0.5000 (1 / 2) [0.0945, 0.9055] |" in text
    assert "| ocr | dense | 1 | 0.0100 | 0.0100 (1 / 100) [0.0018, 0.0545] |" in text
    assert "multi-tag cases appear once in each applicable row" in text


def test_micro_rates_are_not_invented_for_legacy_scores_without_counts() -> None:
    aggregate = aggregate_case_scores((_case(),))

    assert aggregate.micro_cer is None
    assert aggregate.micro_wer is None
    assert aggregate.character_errors is None
    assert aggregate.word_errors is None


def test_micro_wer_uses_applicable_cases_and_counts_are_all_or_nothing() -> None:
    english = replace(
        _case(case_id="english", cer=0.5, tags=()),
        wer=0.5,
        character_errors=1,
        reference_characters=2,
        word_errors=1,
        reference_words=2,
    )
    chinese = replace(
        _case(case_id="chinese", cer=0.5, tags=()),
        language="zh-Hant",
        wer=None,
        word_tokenizer="not-applicable",
        character_errors=2,
        reference_characters=4,
    )
    applicable = aggregate_case_scores((english, chinese))

    assert applicable.micro_cer == 3 / 6
    assert applicable.micro_wer == 1 / 2
    assert applicable.wer_case_count == 1

    missing_character_count = replace(english, case_id="legacy-cer", character_errors=None)
    partial = aggregate_case_scores((english, missing_character_count))
    assert partial.micro_cer is None
    assert partial.micro_wer == 2 / 4

    text = render_report(
        (
            BenchmarkReport(
                engine="ocr",
                cases=(english, chinese),
                aggregate=applicable,
            ),
        ),
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )
    assert (
        "| ocr | (untagged) | 2 | 0.5000 [0.5000, 0.5000] | 0.5000 (3 / 6) [0.1876, 0.8124] |"
        in text
    )
    assert "| english | en | - | 0.5000 (1 / 2 chars) [0.0945, 0.9055] |" in text
    assert (
        "| chinese | zh-Hant | - | 0.5000 (2 / 4 chars) [0.1500, 0.8500] | N/A (`not-applicable`) |"
        in text
    )


def test_report_renders_confidence_intervals_for_macro_and_micro_rates() -> None:
    """A single mean must not hide uncertainty: macro and micro get deterministic CIs."""

    small = replace(
        _case(case_id="small", cer=0.5, tags=("body",)),
        wer=1.0,
        character_errors=1,
        reference_characters=2,
        word_errors=1,
        reference_words=1,
    )
    large = replace(
        _case(case_id="large", cer=0.01, tags=("dense",)),
        wer=0.01,
        character_errors=1,
        reference_characters=100,
        word_errors=1,
        reference_words=100,
    )
    aggregate = aggregate_case_scores((small, large))
    text = render_report(
        (BenchmarkReport(engine="ocr", cases=(small, large), aggregate=aggregate),),
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    # Macro intervals use the per-case rates; micro intervals use the raw counts.
    assert (
        "| ocr | 2 | 0.2550 [0.0000, 0.7352] | 0.0196 (2 / 102) [0.0054, 0.0687] | "
        "0.5050 [0.0000, 1.4752] (2 / 2) | 0.0198 (2 / 101) [0.0054, 0.0693] |" in text
    )
    # Per-case cells disclose their own count-based interval.
    assert "| small | en | body | 0.5000 (1 / 2 chars) [0.0945, 0.9055] |" in text
    assert "| large | en | dense | 0.0100 (1 / 100 chars) [0.0018, 0.0545] |" in text
    # Tag strata carry the same intervals.
    assert "| ocr | body | 1 | 0.5000 | 0.5000 (1 / 2) [0.0945, 0.9055] |" in text
    assert "95% CI" in text
    assert "Wilson" in text


def test_report_marks_macro_interval_unavailable_for_a_single_case() -> None:
    aggregate = aggregate_case_scores((_case(),))
    text = render_report(
        (BenchmarkReport(engine="ocr", cases=(_case(),), aggregate=aggregate),),
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    # One case has no dispersion estimate, so the macro cell keeps the bare mean.
    assert "| ocr | 1 | 0.2500 | N/A |" in text
    assert "at least two cases" in text


def test_report_stratifies_results_by_declared_language() -> None:
    """#68: language coverage must be visible, not hidden in an overall mean."""

    english = replace(
        _case(case_id="en-1", cer=0.1, tags=()),
        language="en",
        wer=0.2,
        character_errors=1,
        reference_characters=10,
        word_errors=1,
        reference_words=5,
    )
    chinese = replace(
        _case(case_id="zh-1", cer=0.4, tags=()),
        language="zh-Hant",
        wer=None,
        word_tokenizer="not-applicable",
        character_errors=2,
        reference_characters=5,
    )
    aggregate = aggregate_case_scores((english, chinese))
    text = render_report(
        (BenchmarkReport(engine="ocr", cases=(english, chinese), aggregate=aggregate),),
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "- declared languages: `en` (1), `zh-Hant` (1)" in text.splitlines()
    assert "## Results by language" in text
    assert (
        "| ocr | en | 1 | 0.1000 | 0.1000 (1 / 10) [0.0179, 0.4042] | "
        "0.2000 (1 / 1) | 0.2000 (1 / 5) [0.0362, 0.6245] |" in text
    )
    assert (
        "| ocr | zh-Hant | 1 | 0.4000 | 0.4000 (2 / 5) [0.1176, 0.7693] | "
        "N/A (0 / 1) | N/A |" in text
    )
    assert "declared manifest metadata" in text


def test_report_shows_spatial_line_counts_only_when_declared() -> None:
    geo = replace(_case(), spatial_missing_lines=1, spatial_extra_lines=2)
    aggregate = aggregate_case_scores((geo,))
    text = render_report(
        (BenchmarkReport(engine="ocr", cases=(geo,), aggregate=aggregate),),
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "| Spatial Missing | Spatial Extra |" in text
    assert "| ocr | 1 |" in text and "| 1 | 2 |" in text
    assert "| case-1 | en | body,punctuation |" in text and "| 1 | 2 |" in text
    assert "IoU >= 0.5" in text

    plain = aggregate_case_scores((_case(),))
    text = render_report(
        (BenchmarkReport(engine="ocr", cases=(_case(),), aggregate=plain),),
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )
    assert "| - | - |" in text
