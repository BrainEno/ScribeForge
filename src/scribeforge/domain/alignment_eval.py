"""Evaluation of line alignment over hand-labelled layout samples (issue #79).

The acceptance criterion for complex-layout alignment is to *report* match
accuracy and reading-order error rates on annotated samples, and to prove that
undetermined alignments enter human review. This module makes those numbers a
production artifact instead of test-only arithmetic: each sample declares the
expected group mapping at the original OCR line-index level, the sample is run
through the real :func:`align_pages` plus the risk/review stages, and the
predicted groups are compared with the labelled ones — never page-wide
concatenated text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from scribeforge.domain.alignment import (
    AlignmentStatus,
    LineAlignment,
    PageAlignment,
    align_pages,
)
from scribeforge.domain.ocr import PageOCRResult
from scribeforge.domain.risk import assess_page, review_candidates


@dataclass(frozen=True, slots=True)
class ExpectedGroup:
    """One labelled mapping between original primary and secondary line indexes."""

    primary: tuple[int, ...]
    secondary: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.primary or not self.secondary:
            raise ValueError("an expected group needs lines from both engines")


@dataclass(frozen=True, slots=True)
class AlignmentSample:
    """One annotated page pair plus its expected group mapping."""

    name: str
    primary: PageOCRResult
    secondary: PageOCRResult
    expected_groups: tuple[ExpectedGroup, ...]
    #: Column label per original line index, used for cross-column error counts.
    primary_columns: tuple[str, ...] = ()
    secondary_columns: tuple[str, ...] = ()
    #: When true, the sample is expected to stay ambiguous (never force-paired).
    expect_ambiguity: bool = False


@dataclass(frozen=True, slots=True)
class SampleEvaluation:
    """Group-level metrics for one sample; groups are compared, not page text."""

    name: str
    true_positive: int
    false_positive: int
    false_negative: int
    reading_order_errors: int
    cross_column_errors: int
    unresolved_ambiguities: int
    one_to_many: int
    many_to_one: int
    review_candidates: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 1.0

    @property
    def f1(self) -> float:
        denominator = self.precision + self.recall
        return 2 * self.precision * self.recall / denominator if denominator else 0.0


@dataclass(frozen=True, slots=True)
class AlignmentEvaluation:
    """Aggregate of per-sample evaluations."""

    samples: tuple[SampleEvaluation, ...] = field(default_factory=tuple)

    @property
    def totals(self) -> SampleEvaluation:
        return SampleEvaluation(
            name="aggregate",
            true_positive=sum(item.true_positive for item in self.samples),
            false_positive=sum(item.false_positive for item in self.samples),
            false_negative=sum(item.false_negative for item in self.samples),
            reading_order_errors=sum(item.reading_order_errors for item in self.samples),
            cross_column_errors=sum(item.cross_column_errors for item in self.samples),
            unresolved_ambiguities=sum(item.unresolved_ambiguities for item in self.samples),
            one_to_many=sum(item.one_to_many for item in self.samples),
            many_to_one=sum(item.many_to_one for item in self.samples),
            review_candidates=sum(item.review_candidates for item in self.samples),
        )


def _group_of(pair: LineAlignment) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return pair.primary_line_indexes, pair.secondary_line_indexes


def evaluate_alignment_sample(
    sample: AlignmentSample, *, alignment: PageAlignment | None = None
) -> SampleEvaluation:
    """Run the real alignment and risk stages, then compare predicted groups."""

    if alignment is None:
        alignment = align_pages(sample.primary, sample.secondary)
    pairs = alignment.pairs
    regular = [pair for pair in pairs if pair.status is AlignmentStatus.MATCHED]
    actual = {_group_of(pair) for pair in regular}
    expected = {(group.primary, group.secondary) for group in sample.expected_groups}

    expected_order = [(group.primary, group.secondary) for group in sample.expected_groups]
    expected_positions = {group: index for index, group in enumerate(expected_order)}
    actual_order = [_group_of(pair) for pair in regular]
    reading_positions = [
        expected_positions[group] for group in actual_order if group in expected_positions
    ]
    reading_order_errors = sum(
        earlier > later
        for index, earlier in enumerate(reading_positions)
        for later in reading_positions[index + 1 :]
    )

    cross_column_errors = 0
    if sample.primary_columns and sample.secondary_columns:
        for pair in regular:
            primary_columns = {sample.primary_columns[index] for index in pair.primary_line_indexes}
            secondary_columns = {
                sample.secondary_columns[index] for index in pair.secondary_line_indexes
            }
            if (
                len(primary_columns) != 1
                or len(secondary_columns) != 1
                or primary_columns != secondary_columns
            ):
                cross_column_errors += 1

    risks = assess_page(alignment, sample.primary, sample.secondary)
    review_count = len(
        review_candidates(alignment, risks, sample.primary, sample.secondary)
    )

    return SampleEvaluation(
        name=sample.name,
        true_positive=len(actual & expected),
        false_positive=len(actual - expected),
        false_negative=len(expected - actual),
        reading_order_errors=reading_order_errors,
        cross_column_errors=cross_column_errors,
        unresolved_ambiguities=sum(
            pair.status is AlignmentStatus.AMBIGUOUS for pair in pairs
        ),
        one_to_many=sum(
            len(pair.primary_line_indexes) == 1 and len(pair.secondary_line_indexes) > 1
            for pair in regular
        ),
        many_to_one=sum(
            len(pair.primary_line_indexes) > 1 and len(pair.secondary_line_indexes) == 1
            for pair in regular
        ),
        review_candidates=review_count,
    )


def evaluate_alignment_samples(
    samples: Sequence[AlignmentSample],
) -> AlignmentEvaluation:
    """Evaluate every sample through the production pipeline stages."""

    return AlignmentEvaluation(
        samples=tuple(evaluate_alignment_sample(sample) for sample in samples)
    )


def _row(metrics: SampleEvaluation) -> str:
    return (
        f"| {metrics.name} | {metrics.true_positive} | {metrics.false_positive} | "
        f"{metrics.false_negative} | {metrics.precision:.4f} | {metrics.recall:.4f} | "
        f"{metrics.f1:.4f} | {metrics.reading_order_errors} | {metrics.cross_column_errors} | "
        f"{metrics.unresolved_ambiguities} | {metrics.one_to_many} | {metrics.many_to_one} | "
        f"{metrics.review_candidates} |"
    )


def render_alignment_report(evaluation: AlignmentEvaluation) -> str:
    """Deterministic Markdown report of match accuracy and order error rates."""

    lines = [
        "# Alignment evaluation",
        "",
        (
            "Group-level comparison of predicted vs hand-labelled line-index groups; "
            "undetermined regions count as ambiguities and their review-queue entries are "
            "reported separately."
        ),
        "",
        (
            "| Sample | TP | FP | FN | Precision | Recall | F1 | order_errors "
            "| cross_column | ambiguities | 1:N | N:1 | review_queue |"
        ),
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_row(sample) for sample in evaluation.samples)
    lines.append(_row(evaluation.totals))
    lines.append("")
    return "\n".join(lines)
