"""Corpus validation for the real-page benchmark.

A benchmark result is evidence only if every case actually has the page image and
the manually verified ground truth the manifest claims. Validation runs before any
engine is started, so a typo cannot silently shrink the corpus. File references
must resolve inside the selected corpus root; a manifest cannot read arbitrary
files through absolute paths, traversal, or escaping symlinks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from scribeforge.benchmark.manifest import BenchmarkManifest


@dataclass(frozen=True, slots=True)
class CorpusProblem:
    """One reason a benchmark case cannot be scored."""

    case_id: str
    problem: str


def _resolve_corpus_path(
    root: Path, value: str, label: str
) -> tuple[Path | None, str | None]:
    """Resolve a manifest file reference and reject anything outside root."""

    if not value.strip():
        return None, f"{label} path must not be empty"

    path = Path(value)
    windows_path = PureWindowsPath(value)
    if path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        return None, f"absolute {label} path is not allowed: {value}"

    try:
        resolved = (root / path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None, f"invalid {label} path: {value}"

    try:
        resolved.relative_to(root)
    except ValueError:
        return None, f"{label} path escapes corpus root: {value}"
    return resolved, None


def validate_corpus(
    manifest: BenchmarkManifest, root: Path
) -> tuple[CorpusProblem, ...]:
    """Return every unsafe, missing, or empty image/ground-truth reference."""

    if not manifest.cases:
        return (CorpusProblem("", "manifest contains no cases"),)

    corpus_root = root.resolve()
    problems: list[CorpusProblem] = []
    for case in manifest.cases:
        image, image_problem = _resolve_corpus_path(
            corpus_root, case.image, "image"
        )
        if image_problem is not None:
            problems.append(CorpusProblem(case.id, image_problem))
        elif image is not None:
            if not image.is_file():
                problems.append(CorpusProblem(case.id, f"missing image: {case.image}"))
            elif image.stat().st_size == 0:
                problems.append(CorpusProblem(case.id, f"empty image: {case.image}"))

        truth, truth_problem = _resolve_corpus_path(
            corpus_root, case.ground_truth, "ground truth"
        )
        if truth_problem is not None:
            problems.append(CorpusProblem(case.id, truth_problem))
        elif truth is not None:
            if not truth.is_file():
                problems.append(
                    CorpusProblem(case.id, f"missing ground truth: {case.ground_truth}")
                )
            elif not truth.read_text(encoding="utf-8").strip():
                problems.append(
                    CorpusProblem(case.id, f"ground truth is blank: {case.ground_truth}")
                )
    return tuple(problems)
