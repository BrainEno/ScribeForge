from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from scribeforge.benchmark.manifest import BenchmarkManifest
from scribeforge.benchmark.metrics import (
    character_error_rate,
    line_error_metrics,
    punctuation_accuracy,
    reading_order_errors,
    scoring_text,
    word_error_rate,
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
    applicable_wer = [score.wer for score in scores if score.wer is not None]
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
        scores.append(
            CaseScore(
                case_id=case.id,
                language=case.language,
                tags=case.tags,
                cer=character_error_rate(reference, hypothesis),
                wer=word_error_rate(reference, hypothesis, language=case.language),
                word_tokenizer=word_tokenizer_for_language(case.language),
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
