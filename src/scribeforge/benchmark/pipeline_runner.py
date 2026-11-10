"""Pipeline-level OCR benchmark for the conservative dual-engine variant.

Each case is processed through the production alignment, risk and decision stages
in an isolated temporary SQLite store. Only accepted decisions are scored;
unresolved pairs contribute no guessed text and are reported explicitly.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

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
from scribeforge.benchmark.runner import (
    BenchmarkReport,
    CaseScore,
    aggregate_case_scores,
)
from scribeforge.domain.ocr import OCREngine
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.storage.sqlite import SQLiteStore


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_pipeline_benchmark(
    manifest: BenchmarkManifest,
    root: Path,
    primary_engine: OCREngine,
    secondary_engine: OCREngine,
    *,
    reviewer: VLMReviewer | None = None,
    label: str | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> BenchmarkReport:
    """Score accepted pipeline text over every manifest case.

    A temporary project is created per case so benchmark evidence cannot alter a
    user's project store. With no reviewer configured, only independent agreement
    is accepted; disagreement and missing-engine pairs remain unresolved. A
    reviewer may be injected by tests or future runtime integrations, but this
    function does not construct or pretend to provide a production VLM runtime.
    """

    scores: list[CaseScore] = []
    for case in manifest.cases:
        image_path = (root / case.image).resolve()
        reference = scoring_text((root / case.ground_truth).read_text(encoding="utf-8"))
        with Image.open(image_path) as image:
            width, height = image.size

        with TemporaryDirectory(prefix="scribeforge-benchmark-") as temporary:
            temporary_root = Path(temporary)
            store = SQLiteStore(temporary_root / "project.sqlite3")
            store.initialize()
            project_id = store.create_project(
                case.id,
                str(image_path),
                _sha256(image_path),
            )
            store.add_page(project_id, 0, str(image_path), width, height)

            started = clock()
            outcome = process_book(
                store,
                project_id,
                primary_engine=primary_engine,
                secondary_engine=secondary_engine,
                reviewer=reviewer,
                crops_dir=temporary_root / "crops",
            )
            elapsed = clock() - started
            if outcome.failed_pages:
                details = "; ".join(
                    f"page {page_index}: {message}" for page_index, message in outcome.failed_pages
                )
                raise RuntimeError(f"case {case.id} pipeline failed: {details}")

            verification_id = store.latest_verification_id(project_id, 0)
            verification = store.load_verification(verification_id)
            accepted_text: list[str] = []
            accepted_pairs = 0
            unresolved_pairs = 0
            for pair_index in range(len(verification.alignment.pairs)):
                decision = store.latest_decision_for_verification(
                    project_id, 0, pair_index, verification_id=verification_id
                )
                if decision is None:
                    unresolved_pairs += 1
                    continue
                accepted_pairs += 1
                accepted_text.append(decision.selected_text)

            hypothesis = scoring_text("\n".join(accepted_text))
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
                    wer=(None if word_counts is None else error_rate_from_counts(*word_counts)),
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
                    accepted_pairs=accepted_pairs,
                    unresolved_pairs=unresolved_pairs,
                    direction=case.direction,
                )
            )

    aggregate = aggregate_case_scores(scores, include_decision_counts=True)
    variant = label or ("dual-engine+vlm" if reviewer is not None else "dual-engine")
    return BenchmarkReport(engine=variant, cases=tuple(scores), aggregate=aggregate)
