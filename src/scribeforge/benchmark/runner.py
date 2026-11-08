from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from scribeforge.benchmark.manifest import BenchmarkManifest
from scribeforge.benchmark.metrics import (
    character_error_counts,
    error_rate_from_counts,
    line_error_metrics,
    punctuation_accuracy,
    reading_order_errors,
    scoring_text,
    word_error_counts,
    word_tokenizer_for_language,
)
from scribeforge.domain.ocr import OCREngine, page_text


@dataclass(frozen=True, slots=True)
class CaseScore:
    """One scored page, including the case metadata needed to slice the report."""

    case_id: str
    language: str
    tags: tuple[str, ...]
    cer: float
    wer: float | None
    punctuation_accuracy: float
    missing_lines: int
    extra_lines: int
    reading_order_errors: int
    elapsed_seconds: float
    accepted_pairs: int | None = None
    unresolved_pairs: int | None = None
    direction: str | None = None
    changed_lines: int = 0
    word_tokenizer: str = "whitespace-v1"
    character_errors: int | None = None
    reference_characters: int | None = None
    word_errors: int | None = None
    reference_words: int | None = None

    @property
    def net_missing_lines(self) -> int:
        return self.missing_lines - self.changed_lines

    @property
    def net_extra_lines(self) -> int:
        return self.extra_lines - self.changed_lines


@dataclass(frozen=True, slots=True)
class AggregateScore:
    case_count: int
    mean_cer: float
    mean_wer: float | None
    mean_punctuation_accuracy: float
    missing_lines: int
    extra_lines: int
    reading_order_errors: int
    elapsed_seconds: float
    pages_per_minute: float
    accepted_pairs: int | None = None
    unresolved_pairs: int | None = None
    wer_case_count: int = 0
    changed_lines: int = 0
    character_errors: int | None = None
    reference_characters: int | None = None
    word_errors: int | None = None
    reference_words: int | None = None

    @property
    def micro_cer(self) -> float | None:
        if self.character_errors is None or self.reference_characters is None:
            return None
        return error_rate_from_counts(self.character_errors, self.reference_characters)

    @property
    def micro_wer(self) -> float | None:
        if self.word_errors is None or self.reference_words is None:
            return None
        return error_rate_from_counts(self.word_errors, self.reference_words)

    @property
    def net_missing_lines(self) -> int:
        return self.missing_lines - self.changed_lines

    @property
    def net_extra_lines(self) -> int:
        return self.extra_lines - self.changed_lines


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    engine: str
    cases: tuple[CaseScore, ...]
    aggregate: AggregateScore


def aggregate_case_scores(
    scores: Sequence[CaseScore], *, include_decision_counts: bool = False
) -> AggregateScore:
    """Aggregate pages without treating not-applicable WER values as zero."""

    count = len(scores)
    applicable_scores = [score for score in scores if score.wer is not None]
    applicable_wer = [score.wer for score in applicable_scores if score.wer is not None]
    complete_characters = all(
        score.character_errors is not None and score.reference_characters is not None
        for score in scores
    )
    complete_words = bool(applicable_scores) and all(
        score.word_errors is not None and score.reference_words is not None
        for score in applicable_scores
    )
    total_seconds = sum(score.elapsed_seconds for score in scores)
    return AggregateScore(
        case_count=count,
        mean_cer=sum(score.cer for score in scores) / count if count else 0.0,
        mean_wer=(sum(applicable_wer) / len(applicable_wer) if applicable_wer else None),
        mean_punctuation_accuracy=(
            sum(score.punctuation_accuracy for score in scores) / count if count else 1.0
        ),
        missing_lines=sum(score.missing_lines for score in scores),
        extra_lines=sum(score.extra_lines for score in scores),
        reading_order_errors=sum(score.reading_order_errors for score in scores),
        elapsed_seconds=total_seconds,
        pages_per_minute=60.0 * count / total_seconds if total_seconds > 0 else 0.0,
        accepted_pairs=(
            sum(score.accepted_pairs or 0 for score in scores)
            if include_decision_counts
            else None
        ),
        unresolved_pairs=(
            sum(score.unresolved_pairs or 0 for score in scores)
            if include_decision_counts
            else None
        ),
        wer_case_count=len(applicable_wer),
        changed_lines=sum(score.changed_lines for score in scores),
        character_errors=(
            sum(score.character_errors or 0 for score in scores)
            if complete_characters
            else None
        ),
        reference_characters=(
            sum(score.reference_characters or 0 for score in scores)
            if complete_characters
            else None
        ),
        word_errors=(
            sum(score.word_errors or 0 for score in applicable_scores)
            if complete_words
            else None
        ),
        reference_words=(
            sum(score.reference_words or 0 for score in applicable_scores)
            if complete_words
            else None
        ),
    )


def run_benchmark(
    manifest: BenchmarkManifest,
    root: Path,
    engine: OCREngine,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> BenchmarkReport:
    """Score one engine over every case, timing each page.

    ``clock`` is injectable so tests can assert durations exactly instead of
    depending on machine speed.
    """

    scores: list[CaseScore] = []
    for page_index, case in enumerate(manifest.cases):
        reference = scoring_text(
            (root / case.ground_truth).read_text(encoding="utf-8")
        )
        started = clock()
        result = engine.analyze_page(str(root / case.image), page_index)
        elapsed = clock() - started
        hypothesis = scoring_text(page_text(result))
        reference_lines = reference.splitlines()
        hypothesis_lines = hypothesis.splitlines()
        line_metrics = line_error_metrics(reference_lines, hypothesis_lines)
        character_counts = character_error_counts(reference, hypothesis)
        word_counts = word_error_counts(reference, hypothesis, language=case.language)
        scores.append(
            CaseScore(
                case_id=case.id,
                language=case.language,
                tags=case.tags,
                cer=error_rate_from_counts(*character_counts),
                wer=(
                    None
                    if word_counts is None
                    else error_rate_from_counts(*word_counts)
                ),
                word_tokenizer=word_tokenizer_for_language(case.language),
                character_errors=character_counts[0],
                reference_characters=character_counts[1],
                word_errors=None if word_counts is None else word_counts[0],
                reference_words=None if word_counts is None else word_counts[1],
                punctuation_accuracy=punctuation_accuracy(reference, hypothesis),
                missing_lines=line_metrics.missing_lines,
                extra_lines=line_metrics.extra_lines,
                changed_lines=line_metrics.changed_lines,
                reading_order_errors=reading_order_errors(reference_lines, hypothesis_lines),
                elapsed_seconds=elapsed,
                direction=case.direction,
            )
        )

    aggregate = aggregate_case_scores(scores)
    return BenchmarkReport(engine=engine.name, cases=tuple(scores), aggregate=aggregate)
