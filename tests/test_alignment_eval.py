"""#79 acceptance artifact: production alignment evaluation over labelled samples.

`domain/alignment_eval.py` turns hand-labelled layout samples into the
reported match accuracy / order error rate / review-queue numbers that
issue #79 requires, instead of keeping the metrics inside test code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.alignment_eval import (
    AlignmentSample,
    ExpectedGroup,
    evaluate_alignment_sample,
    evaluate_alignment_samples,
    load_alignment_samples,
    render_alignment_report,
)
from scribeforge.domain.ocr import (
    BoundingBox,
    OCRLine,
    OCRToken,
    PageDirection,
    PageOCRResult,
)


def _result(
    engine: str,
    lines: list[tuple[str, float, float, float, float]],
    *,
    direction: PageDirection = PageDirection.HORIZONTAL_LR,
) -> PageOCRResult:
    return PageOCRResult(
        page_index=0,
        engine=engine,
        engine_version="1",
        lines=tuple(
            OCRLine(
                text,
                BoundingBox(x, y, w, h),
                (OCRToken(text, 0.9, BoundingBox(x, y, w, h)),),
            )
            for text, x, y, w, h in lines
        ),
        direction=direction,
        direction_declared=True,
    )


def _two_column_sample() -> AlignmentSample:
    primary = _result(
        "mineru",
        [
            ("左栏第一行", 0.10, 0.20, 0.40, 0.05),
            ("右栏第一行", 0.60, 0.20, 0.40, 0.05),
            ("左栏第二行", 0.10, 0.35, 0.40, 0.05),
            ("右栏第二行", 0.60, 0.35, 0.40, 0.05),
        ],
    )
    secondary = _result(
        "paddleocr",
        [
            ("左栏第一行", 0.10, 0.21, 0.40, 0.05),
            ("右栏第一行", 0.60, 0.21, 0.40, 0.05),
            ("左栏第二行", 0.10, 0.36, 0.40, 0.05),
            ("右栏第二行", 0.60, 0.36, 0.40, 0.05),
        ],
    )
    return AlignmentSample(
        name="two-column",
        primary=primary,
        secondary=secondary,
        expected_groups=(
            ExpectedGroup((0,), (0,)),
            ExpectedGroup((1,), (1,)),
            ExpectedGroup((2,), (2,)),
            ExpectedGroup((3,), (3,)),
        ),
        primary_columns=("left", "right", "left", "right"),
        secondary_columns=("left", "right", "left", "right"),
    )


def test_perfect_sample_scores_full_precision_and_no_order_errors() -> None:
    metrics = evaluate_alignment_sample(_two_column_sample())

    assert (metrics.true_positive, metrics.false_positive, metrics.false_negative) == (4, 0, 0)
    assert (metrics.precision, metrics.recall, metrics.f1) == (1.0, 1.0, 1.0)
    assert metrics.reading_order_errors == 0
    assert metrics.cross_column_errors == 0
    assert metrics.unresolved_ambiguities == 0
    assert metrics.review_candidates == 0


def test_wrong_group_counts_false_positive_and_false_negative() -> None:
    sample = _two_column_sample()
    wrong = AlignmentSample(
        name="wrong",
        primary=sample.primary,
        secondary=sample.secondary,
        expected_groups=(
            ExpectedGroup((0,), (1,)),  # swapped columns: not the labelled mapping
            ExpectedGroup((1,), (0,)),
            ExpectedGroup((2,), (2,)),
            ExpectedGroup((3,), (3,)),
        ),
    )
    metrics = evaluate_alignment_sample(wrong)

    assert metrics.true_positive == 2
    assert metrics.false_positive == 2
    assert metrics.false_negative == 2
    # The predicted pairs themselves stay column-consistent; only the labels
    # were swapped, so the cross-column metric is about predictions, not labels.
    assert metrics.cross_column_errors == 0
    assert metrics.precision == pytest.approx(0.5)
    assert metrics.recall == pytest.approx(0.5)


def test_one_to_many_and_many_to_one_groups_are_counted() -> None:
    primary = _result(
        "mineru",
        [("春眠不觉晓", 0.1, 0.2, 0.5, 0.04), ("处处闻啼鸟", 0.1, 0.4, 0.5, 0.04)],
    )
    secondary = _result(
        "paddleocr",
        [
            ("春眠", 0.1, 0.18, 0.5, 0.03),
            ("不觉晓", 0.1, 0.23, 0.5, 0.03),
            ("处处闻啼鸟", 0.1, 0.4, 0.5, 0.04),
        ],
    )
    sample = AlignmentSample(
        name="split-merge",
        primary=primary,
        secondary=secondary,
        expected_groups=(
            ExpectedGroup((0,), (0, 1)),
            ExpectedGroup((1,), (2,)),
        ),
    )
    metrics = evaluate_alignment_sample(sample)

    assert metrics.f1 == 1.0
    assert (metrics.one_to_many, metrics.many_to_one) == (1, 0)


def test_ambiguous_sample_reports_unresolved_ambiguity_and_review() -> None:
    # One primary line competing between two identical secondary lines:
    # the aligner must not force a pair, and the region must enter review.
    primary = _result("mineru", [("相同文字", 0.10, 0.30, 0.60, 0.04)])
    secondary = _result(
        "paddleocr",
        [
            ("相同文字", 0.10, 0.29, 0.60, 0.04),
            ("相同文字", 0.10, 0.31, 0.60, 0.04),
        ],
    )
    sample = AlignmentSample(
        name="ambiguous",
        primary=primary,
        secondary=secondary,
        expected_groups=(),
        expect_ambiguity=True,
    )
    metrics = evaluate_alignment_sample(sample)

    assert metrics.unresolved_ambiguities >= 1
    assert metrics.false_positive == 0  # no forced match
    assert metrics.review_candidates >= 1


def test_lone_disagreement_is_force_paired_but_queued_for_review() -> None:
    primary = _result("mineru", [("完全不同的文本内容", 0.10, 0.20, 0.80, 0.05)])
    secondary = _result("paddleocr", [("totally different text", 0.10, 0.21, 0.80, 0.05)])
    sample = AlignmentSample(
        name="disagreement",
        primary=primary,
        secondary=secondary,
        expected_groups=(),
    )
    metrics = evaluate_alignment_sample(sample)

    assert metrics.review_candidates >= 1


def test_rotated_geometry_sample_scores_no_forced_match() -> None:
    """#79: rotated text via the production eval — no FP, review-queued."""

    primary = _result(
        "mineru",
        [("旋轉文字行", 0.10, 0.20, 0.80, 0.05), ("正常行", 0.10, 0.50, 0.60, 0.04)],
    )
    secondary = _result(
        "paddleocr",
        [("旋轉文字行", 0.10, 0.20, 0.05, 0.80), ("正常行", 0.10, 0.50, 0.60, 0.04)],
    )
    sample = AlignmentSample(
        name="rotated",
        primary=primary,
        secondary=secondary,
        expected_groups=(),
    )
    metrics = evaluate_alignment_sample(sample)

    # Nothing is force-matched (no false positives), and the page is queued
    # for human review instead of receiving a wrong automatic mapping.
    assert (metrics.true_positive, metrics.false_positive, metrics.false_negative) == (0, 0, 0)
    assert metrics.review_candidates >= 1


def test_aggregate_evaluation_and_markdown_report() -> None:
    evaluation = evaluate_alignment_samples([_two_column_sample()])
    totals = evaluation.totals

    assert totals.precision == totals.recall == totals.f1 == 1.0
    assert totals.reading_order_errors == 0
    assert len(evaluation.samples) == 1

    report = render_alignment_report(evaluation)
    assert "# Alignment evaluation" in report
    assert "| two-column |" in report
    assert "| aggregate |" in report
    assert "order_errors" in report
    # Deterministic: rendering twice is byte-identical.
    assert render_alignment_report(evaluation) == report


FIXTURES = Path(__file__).parent / "fixtures" / "alignment_groundtruth.json"


def test_load_alignment_samples_parses_the_hand_labelled_fixture_file() -> None:
    samples = load_alignment_samples(FIXTURES)

    assert len(samples) == 11
    names = [sample.name for sample in samples]
    assert "rotated-text-is-not-force-matched" in names
    rotated = next(s for s in samples if s.name == "rotated-text-is-not-force-matched")
    assert rotated.expected_groups == ()
    assert rotated.expect_ambiguity is False
    assert rotated.primary_columns == ("body", "body")
    assert len(rotated.primary.lines) == 2
    assert rotated.primary.direction is PageDirection.HORIZONTAL_LR
    ambiguous = next(s for s in samples if s.expect_ambiguity)
    assert ambiguous.name == "close-score-alternatives-are-ambiguous"


def test_load_alignment_samples_rejects_malformed_files(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="at least one"):
        load_alignment_samples(bad)

    wrong_group = tmp_path / "wrong.json"
    wrong_group.write_text(
        '[{"name": "x", "direction": "horizontal-lr",'
        ' "primary": [{"text": "a", "box": [0, 0, 0.1, 0.1]}],'
        ' "secondary": [{"text": "a", "box": [0, 0, 0.1, 0.1]}],'
        ' "expected_groups": [{"primary": [5], "secondary": [0]}]}]',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="out of range"):
        load_alignment_samples(wrong_group)


def test_loaded_fixtures_evaluate_to_full_accuracy() -> None:
    samples = load_alignment_samples(FIXTURES)
    evaluation = evaluate_alignment_samples([s for s in samples if not s.expect_ambiguity])
    totals = evaluation.totals

    assert (totals.precision, totals.recall, totals.f1) == (1.0, 1.0, 1.0)
    assert totals.reading_order_errors == 0
    assert totals.cross_column_errors == 0
    assert totals.false_positive == 0


COMMITTED_REPORT = Path(__file__).parent.parent / "docs" / "alignment-eval-report.md"


def test_committed_alignment_report_matches_a_fresh_run() -> None:
    # #79 acceptance artifact: the measured match accuracy and order-error
    # rates live in a committed report, and this guard fails if the file
    # drifts from what the production renderer produces today.
    samples = load_alignment_samples(FIXTURES)
    report = render_alignment_report(evaluate_alignment_samples(samples))
    assert COMMITTED_REPORT.is_file(), (
        "docs/alignment-eval-report.md is missing; regenerate it with "
        "`scribeforge alignment-eval --fixtures tests/fixtures/alignment_groundtruth.json`"
    )
    assert COMMITTED_REPORT.read_text(encoding="utf-8") == report
