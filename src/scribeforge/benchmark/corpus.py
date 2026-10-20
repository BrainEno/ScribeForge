"""Corpus validation for the real-page benchmark.

A benchmark result is evidence only if every case actually has the page image and
the manually verified ground truth the manifest claims. Validation runs before any
engine is started, so a typo cannot silently shrink the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scribeforge.benchmark.manifest import BenchmarkManifest


@dataclass(frozen=True, slots=True)
class CorpusProblem:
    """One reason a benchmark case cannot be scored."""

    case_id: str
    problem: str


def validate_corpus(
    manifest: BenchmarkManifest, root: Path
) -> tuple[CorpusProblem, ...]:
    """Return every missing or empty image/ground-truth pair in the manifest."""

    if not manifest.cases:
        return (CorpusProblem("", "manifest contains no cases"),)

    problems: list[CorpusProblem] = []
    for case in manifest.cases:
        image = root / case.image
        if not image.is_file():
            problems.append(CorpusProblem(case.id, f"missing image: {case.image}"))
        elif image.stat().st_size == 0:
            problems.append(CorpusProblem(case.id, f"empty image: {case.image}"))

        truth = root / case.ground_truth
        if not truth.is_file():
            problems.append(
                CorpusProblem(case.id, f"missing ground truth: {case.ground_truth}")
            )
        elif not truth.read_text(encoding="utf-8").strip():
            problems.append(
                CorpusProblem(case.id, f"ground truth is blank: {case.ground_truth}")
            )
    return tuple(problems)
