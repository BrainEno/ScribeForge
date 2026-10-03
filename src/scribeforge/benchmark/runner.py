from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scribeforge.benchmark.manifest import BenchmarkManifest
from scribeforge.benchmark.metrics import (
    character_error_rate,
    line_error_counts,
    punctuation_accuracy,
    word_error_rate,
)
from scribeforge.domain.ocr import OCREngine, page_text


@dataclass(frozen=True, slots=True)
class CaseScore:
    case_id: str
    cer: float
    wer: float
    punctuation_accuracy: float
    missing_lines: int
    extra_lines: int


@dataclass(frozen=True, slots=True)
class AggregateScore:
    case_count: int
    mean_cer: float
    mean_wer: float
    mean_punctuation_accuracy: float
    missing_lines: int
    extra_lines: int


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    engine: str
    cases: tuple[CaseScore, ...]
    aggregate: AggregateScore


def run_benchmark(manifest: BenchmarkManifest, root: Path, engine: OCREngine) -> BenchmarkReport:
    scores: list[CaseScore] = []
    for page_index, case in enumerate(manifest.cases):
        reference = (root / case.ground_truth).read_text(encoding="utf-8")
        result = engine.analyze_page(str(root / case.image), page_index)
        hypothesis = page_text(result)
        missing, extra = line_error_counts(reference.splitlines(), hypothesis.splitlines())
        scores.append(
            CaseScore(
                case_id=case.id,
                cer=character_error_rate(reference, hypothesis),
                wer=word_error_rate(reference, hypothesis),
                punctuation_accuracy=punctuation_accuracy(reference, hypothesis),
                missing_lines=missing,
                extra_lines=extra,
            )
        )

    count = len(scores)
    aggregate = AggregateScore(
        case_count=count,
        mean_cer=sum(score.cer for score in scores) / count if count else 0.0,
        mean_wer=sum(score.wer for score in scores) / count if count else 0.0,
        mean_punctuation_accuracy=(
            sum(score.punctuation_accuracy for score in scores) / count if count else 1.0
        ),
        missing_lines=sum(score.missing_lines for score in scores),
        extra_lines=sum(score.extra_lines for score in scores),
    )
    return BenchmarkReport(engine=engine.name, cases=tuple(scores), aggregate=aggregate)
