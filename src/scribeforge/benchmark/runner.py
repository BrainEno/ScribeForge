from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scribeforge.benchmark.manifest import BenchmarkManifest
from scribeforge.benchmark.metrics import (
    character_error_rate,
    line_error_counts,
    punctuation_accuracy,
    reading_order_errors,
    scoring_text,
    word_error_rate,
)
from scribeforge.domain.ocr import OCREngine, page_text


@dataclass(frozen=True, slots=True)
class CaseScore:
    """One scored page, including the case metadata needed to slice the report."""

    case_id: str
    language: str
    tags: tuple[str, ...]
    cer: float
    wer: float
    punctuation_accuracy: float
    missing_lines: int
    extra_lines: int
    reading_order_errors: int
    elapsed_seconds: float
    accepted_pairs: int | None = None
    unresolved_pairs: int | None = None
    direction: str | None = None


@dataclass(frozen=True, slots=True)
class AggregateScore:
    case_count: int
    mean_cer: float
    mean_wer: float
    mean_punctuation_accuracy: float
    missing_lines: int
    extra_lines: int
    reading_order_errors: int
    elapsed_seconds: float
    pages_per_minute: float
    accepted_pairs: int | None = None
    unresolved_pairs: int | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    engine: str
    cases: tuple[CaseScore, ...]
    aggregate: AggregateScore


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
        missing, extra = line_error_counts(reference.splitlines(), hypothesis.splitlines())
        scores.append(
            CaseScore(
                case_id=case.id,
                language=case.language,
                tags=case.tags,
                cer=character_error_rate(reference, hypothesis),
                wer=word_error_rate(reference, hypothesis),
                punctuation_accuracy=punctuation_accuracy(reference, hypothesis),
                missing_lines=missing,
                extra_lines=extra,
                reading_order_errors=reading_order_errors(
                    reference.splitlines(), hypothesis.splitlines()
                ),
                elapsed_seconds=elapsed,
                direction=case.direction,
            )
        )

    count = len(scores)
    total_seconds = sum(score.elapsed_seconds for score in scores)
    aggregate = AggregateScore(
        case_count=count,
        mean_cer=sum(score.cer for score in scores) / count if count else 0.0,
        mean_wer=sum(score.wer for score in scores) / count if count else 0.0,
        mean_punctuation_accuracy=(
            sum(score.punctuation_accuracy for score in scores) / count if count else 1.0
        ),
        missing_lines=sum(score.missing_lines for score in scores),
        extra_lines=sum(score.extra_lines for score in scores),
        reading_order_errors=sum(score.reading_order_errors for score in scores),
        elapsed_seconds=total_seconds,
        pages_per_minute=60.0 * count / total_seconds if total_seconds > 0 else 0.0,
    )
    return BenchmarkReport(engine=engine.name, cases=tuple(scores), aggregate=aggregate)
