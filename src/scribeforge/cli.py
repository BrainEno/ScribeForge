from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from scribeforge.adapters.candidate_epub import build_candidate_epub
from scribeforge.adapters.docx import write_docx
from scribeforge.adapters.epub import write_epub
from scribeforge.adapters.openai_compatible_vlm import OpenAICompatibleVLMReviewer
from scribeforge.adapters.runtime_workers import build_runtime_engine
from scribeforge.adapters.sources import open_page_source
from scribeforge.benchmark.corpus import validate_corpus
from scribeforge.benchmark.manifest import BenchmarkManifest, load_manifest
from scribeforge.benchmark.oracle import OracleEngine
from scribeforge.benchmark.pipeline_runner import run_pipeline_benchmark
from scribeforge.benchmark.report import render_report
from scribeforge.benchmark.runner import run_benchmark
from scribeforge.domain.candidate import OcrCandidateBook, OcrCandidatePage
from scribeforge.domain.flow import build_flow
from scribeforge.domain.ocr import OCREngine, PageDirection
from scribeforge.domain.preprocess import (
    PreprocessOperation,
    PreprocessSettings,
    default_settings,
)
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.pipeline.adjudication import record_human_decision, review_detail, review_queue
from scribeforge.pipeline.assembly import UnresolvedContentError, assemble_verified_content
from scribeforge.pipeline.importing import import_source
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.pipeline.rounds import (
    ProgressReport,
    RepairSettings,
    RoundReport,
    progress,
    reader_for,
    repair_book,
)
from scribeforge.pipeline.structure import (
    StructureReport,
    build_book_structure,
    persist_book_structure,
)
from scribeforge.project_audit import audit_project_store
from scribeforge.project_backup import (
    ProjectBackupError,
    create_project_backup,
    restore_project_backup,
    verify_project_backup,
)
from scribeforge.runtime.manager import RuntimeManager
from scribeforge.runtime.models import RuntimeLayout
from scribeforge.runtime.paths import default_runtime_root, runtime_root_for_store
from scribeforge.storage.sqlite import SQLiteStore

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2


@dataclass(frozen=True, slots=True)
class Engines:
    """The OCR engines and optional adjudicator the pipeline runs with."""

    primary: OCREngine
    secondary: OCREngine
    reviewer: VLMReviewer | None = None


EngineFactory = Callable[[Path], Engines]
BenchmarkEngineFactory = Callable[[Path, Sequence[str]], tuple[OCREngine, ...]]

#: The two engines the alignment pipeline needs; benchmark backends that only
#: provide one of them (structure-only Surya) cannot drive ``--include-pipeline``.
_PIPELINE_BACKENDS = ("mineru", "paddleocr")

_BENCHMARK_BACKENDS = {
    "mineru": "mineru_worker.py",
    "paddleocr": "paddle_worker.py",
    "paddleocr-vl": "paddle_vl_worker.py",
    "surya": "surya_worker.py",
}


def _add_repair_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repair-rounds",
        type=int,
        default=0,
        help=(
            "Re-read disputed lines after the first pass, up to N rounds "
            "(0 disables repair; 2 is a good default)."
        ),
    )
    parser.add_argument(
        "--repair-max-pairs",
        type=int,
        default=40,
        help="Maximum disputed pairs to re-read per round (default: 40).",
    )


def _repair_settings(args: argparse.Namespace) -> RepairSettings | None:
    rounds = getattr(args, "repair_rounds", 0)
    if not rounds:
        return None
    return RepairSettings(
        max_rounds=rounds,
        max_pairs_per_round=getattr(args, "repair_max_pairs", 40),
    )


def _add_vlm_arguments(
    parser: argparse.ArgumentParser, *, include_vlm_switch: bool = False
) -> None:
    parser.add_argument(
        "--vlm-endpoint",
        help="OpenAI-compatible vision chat base URL (loopback only by default).",
    )
    parser.add_argument("--vlm-model", help="Vision model identifier served by the endpoint.")
    parser.add_argument(
        "--vlm-api-key-env",
        help="Optional environment variable containing the endpoint bearer token; never pass secrets as CLI values.",
    )
    parser.add_argument(
        "--vlm-timeout",
        type=float,
        default=120.0,
        help="VLM request timeout in seconds (default: 120).",
    )
    parser.add_argument(
        "--allow-remote-vlm",
        action="store_true",
        help="Explicitly allow sending review-crop images to a non-loopback VLM endpoint.",
    )
    if include_vlm_switch:
        parser.add_argument(
            "--include-vlm",
            action="store_true",
            help="Also score variant D using the configured VLM endpoint (requires --include-pipeline).",
        )


def _configured_vlm_reviewer(
    args: argparse.Namespace, *, benchmark: bool = False
) -> VLMReviewer | None:
    endpoint = getattr(args, "vlm_endpoint", None)
    model = getattr(args, "vlm_model", None)
    api_key_env = getattr(args, "vlm_api_key_env", None)
    allow_remote = bool(getattr(args, "allow_remote_vlm", False))

    if benchmark:
        include_vlm = bool(getattr(args, "include_vlm", False))
        include_pipeline = bool(getattr(args, "include_pipeline", False))
        if include_vlm and not include_pipeline:
            raise ValueError("--include-vlm requires --include-pipeline")
        if not include_vlm:
            if endpoint or model or api_key_env is not None or allow_remote:
                raise ValueError(
                    "VLM endpoint options require --include-vlm for the benchmark command"
                )
            return None
        if not endpoint:
            raise ValueError("--vlm-endpoint is required with --include-vlm")
    elif not endpoint and not model:
        if api_key_env is not None or allow_remote:
            raise ValueError("--vlm-api-key-env/--allow-remote-vlm require --vlm-endpoint")
        return None

    if not endpoint:
        raise ValueError("--vlm-endpoint is required when --vlm-model is set")
    if not model:
        raise ValueError("--vlm-model is required when --vlm-endpoint is set")
    if not model.strip():
        raise ValueError("--vlm-model must not be empty")

    api_key = None
    if api_key_env is not None:
        if not api_key_env.strip():
            raise ValueError("--vlm-api-key-env must name an environment variable")
        if api_key_env not in os.environ:
            raise ValueError(f"VLM API key environment variable is not set: {api_key_env}")
        api_key = os.environ[api_key_env]
        if not api_key.strip():
            raise ValueError(f"VLM API key environment variable is empty: {api_key_env}")

    return OpenAICompatibleVLMReviewer(
        endpoint=endpoint,
        model=model,
        api_key=api_key,
        timeout_seconds=args.vlm_timeout,
        allow_remote=allow_remote,
    )


@dataclass(frozen=True, slots=True)
class StoreLayout:
    """On-disk layout of a single-book project store."""

    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "project.sqlite3"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    @property
    def crops_dir(self) -> Path:
        return self.root / "crops"


def default_store_dir(book: Path) -> Path:
    return book.parent / f"{book.stem}.scribeforge"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scribeforge",
        description="Evidence-grade transcription of scanned books.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser(
        "process", help="Import a scanned book and run the pipeline over every page."
    )
    process.add_argument("book", type=Path, help="Scanned PDF or page image to process.")
    process.add_argument(
        "--store",
        type=Path,
        default=None,
        help="Project store directory (default: <book>.scribeforge beside the book).",
    )
    process.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Managed runtime root (default: the per-user ScribeForge runtime folder).",
    )
    process.add_argument(
        "--no-preprocess",
        action="store_true",
        help=(
            "Skip the preprocessing stage and send the raw scans to the OCR engines. "
            "Use this for clean born-digital renders where deskewing only adds work."
        ),
    )
    process.add_argument(
        "--preprocess-binarize",
        action="store_true",
        help=(
            "Threshold pages to black and white after denoising. Off by default: "
            "binarization discards the greyscale evidence reviewers rely on."
        ),
    )
    process.add_argument(
        "--preprocess-max-skew",
        type=float,
        default=None,
        help="Maximum deskew correction in degrees (default: 8).",
    )
    _add_repair_arguments(process)
    _add_vlm_arguments(process)

    setup = subparsers.add_parser(
        "setup", help="Open the first-run wizard for private OCR runtimes and models."
    )
    setup.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Private runtime directory (default: the per-user ScribeForge runtime folder).",
    )

    runtime_status = subparsers.add_parser(
        "runtime-status",
        help="Inspect managed OCR runtimes, models and hardware without modifying them.",
    )
    runtime_status.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Private runtime directory (default: the per-user ScribeForge runtime folder).",
    )
    runtime_status.add_argument(
        "--json", action="store_true", help="Emit a stable JSON status report."
    )

    vlm_check = subparsers.add_parser(
        "vlm-check",
        help="Test a configured VLM endpoint with an explicit image without writing project data.",
    )
    vlm_check.add_argument(
        "--image", type=Path, required=True, help="Image/crop to send for one diagnostic transcription."
    )
    vlm_check.add_argument(
        "--json", action="store_true", help="Emit a machine-readable diagnostic result."
    )
    _add_vlm_arguments(vlm_check)

    status = subparsers.add_parser(
        "status", help="Show the persisted processing jobs of a project store."
    )
    status.add_argument("--store", type=Path, required=True, help="Project store directory.")
    status.add_argument(
        "--json", action="store_true", help="Emit a stable JSON summary for desktop clients."
    )

    structure = subparsers.add_parser(
        "structure",
        help="Derive page roles, printed page numbers and chapters from stored OCR evidence.",
    )
    structure.add_argument("--store", type=Path, required=True, help="Project store directory.")
    structure.add_argument(
        "--engine",
        default="primary",
        help="Engine whose stored run supplies the evidence (default: primary).",
    )
    structure.add_argument(
        "--json", action="store_true", help="Emit a machine-readable structure report."
    )

    relink = subparsers.add_parser(
        "relink-source",
        help="Update a project source path only when its SHA-256 matches.",
    )
    relink.add_argument("--store", type=Path, required=True, help="Project store directory.")
    relink.add_argument(
        "--source", type=Path, required=True, help="Moved source PDF or page image."
    )

    audit = subparsers.add_parser(
        "audit",
        help="Check project database integrity and referenced source assets without repairing them.",
    )
    audit.add_argument("--store", type=Path, required=True, help="Project store directory.")
    audit.add_argument(
        "--json", action="store_true", help="Emit a stable JSON audit report."
    )

    backup = subparsers.add_parser(
        "backup",
        help="Create a verified, self-contained archive of a project store.",
    )
    backup.add_argument("--store", type=Path, required=True, help="Project store directory.")
    backup.add_argument("--out", type=Path, required=True, help="Destination ZIP archive.")
    backup.add_argument(
        "--overwrite", action="store_true", help="Replace an existing archive atomically."
    )
    backup.add_argument("--json", action="store_true", help="Emit a JSON backup report.")

    verify_backup = subparsers.add_parser(
        "verify-backup", help="Verify backup manifest, file hashes, and SQLite integrity."
    )
    verify_backup.add_argument("--backup", type=Path, required=True, help="Backup ZIP archive.")
    verify_backup.add_argument("--json", action="store_true", help="Emit a JSON verification report.")

    restore = subparsers.add_parser(
        "restore", help="Restore a verified backup into a new project directory."
    )
    restore.add_argument("--backup", type=Path, required=True, help="Backup ZIP archive.")
    restore.add_argument("--to", type=Path, required=True, help="New destination directory.")
    restore.add_argument("--json", action="store_true", help="Emit a JSON restore report.")

    repair = subparsers.add_parser(
        "repair",
        help="Re-read the disputed lines of an existing store and accept what agrees.",
    )
    repair.add_argument("--store", type=Path, required=True, help="Project store directory.")
    repair.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Managed runtime root (default: the per-user ScribeForge runtime folder).",
    )
    _add_repair_arguments(repair)
    _add_vlm_arguments(repair)
    repair.add_argument("--json", action="store_true", help="Emit a JSON repair report.")

    review = subparsers.add_parser(
        "review", help="List the page pairs that still need a human decision."
    )
    review.add_argument("--store", type=Path, required=True, help="Project store directory.")

    resolve = subparsers.add_parser(
        "resolve", help="Record a human decision for one page pair."
    )
    resolve.add_argument("--store", type=Path, required=True, help="Project store directory.")
    resolve.add_argument("--page", type=int, required=True, help="Zero-based page index.")
    resolve.add_argument("--pair", type=int, required=True, help="Zero-based aligned-pair index.")
    resolve.add_argument(
        "--text", required=True, help="Literal text the reviewer accepted for the pair."
    )

    confirm = subparsers.add_parser(
        "confirm",
        help="Record that a page carries no text (a blank leaf or a plate).",
    )
    confirm.add_argument("--store", type=Path, required=True, help="Project store directory.")
    confirm.add_argument("--page", type=int, required=True, help="Zero-based page index.")
    confirm.add_argument(
        "--kind",
        choices=("blank", "non_text"),
        default="blank",
        help="blank = empty leaf; non_text = image/plate with no text to read.",
    )
    confirm.add_argument(
        "--reason",
        required=True,
        help="Why this page has no text; recorded in the audit trail.",
    )

    ui = subparsers.add_parser(
        "ui", help="Open the read-only desktop shell for a project store."
    )
    ui.add_argument("--store", type=Path, required=True, help="Project store directory.")

    export = subparsers.add_parser(
        "export", help="Write the verified text of a project to EPUB 3 or DOCX."
    )
    export.add_argument("--store", type=Path, required=True, help="Project store directory.")
    export.add_argument(
        "--out", type=Path, required=True, help="Destination .epub or .docx file."
    )
    export.add_argument(
        "--language", default="und", help="BCP 47 language tag (default: und)."
    )
    export.add_argument(
        "--title", default=None, help="Override the book title (default: project title)."
    )
    export.add_argument("--author", default=None, help="Book author metadata.")
    export.add_argument(
        "--modified",
        default=None,
        help=(
            "ISO 8601 export timestamp for reproducible builds "
            "(default: now; naive values are treated as UTC)."
        ),
    )
    export.add_argument(
        "--strict-acceptance",
        action="store_true",
        help=(
            "Refuse to export while any accepted pair still needs a spot check "
            "(low confidence, unrepresentable characters, or risk above the ceiling)."
        ),
    )
    export.add_argument(
        "--layout",
        choices=("auto", "chapter", "page"),
        default="auto",
        help=(
            "EPUB document layout: chapter documents with page-break markers "
            "(auto = when the book has a chapter tree), or one document per "
            "scanned page (page = facsimile). DOCX always writes one page at a time."
        ),
    )
    export.add_argument(
        "--json", action="store_true", help="Emit a JSON export report."
    )

    candidates = subparsers.add_parser(
        "export-candidates",
        help="Export unverified raw OCR candidates for side-by-side EPUB comparison.",
    )
    candidates.add_argument("--store", type=Path, required=True, help="Project store directory.")
    candidates.add_argument(
        "--out-dir", type=Path, required=True, help="Directory for candidate EPUBs and manifest."
    )
    candidates.add_argument(
        "--project-id", type=int, default=None, help="Project ID (required when store has multiple projects)."
    )
    candidates.add_argument(
        "--engine",
        action="append",
        default=[],
        metavar="NAME",
        help="OCR engine name, repeatable (default: mineru and paddleocr).",
    )
    candidates.add_argument(
        "--language", default="und", help="BCP 47 language tag (default: und)."
    )

    benchmark = subparsers.add_parser(
        "benchmark", help="Score OCR engines against a benchmark corpus manifest."
    )
    benchmark.add_argument(
        "--manifest", type=Path, required=True, help="Benchmark manifest JSON file."
    )
    benchmark.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Corpus root (default: the manifest directory).",
    )
    benchmark.add_argument(
        "--out", type=Path, required=True, help="Destination Markdown report."
    )
    benchmark.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Managed runtime root (default: <root>/runtime).",
    )
    benchmark.add_argument(
        "--engine",
        action="append",
        default=[],
        metavar="NAME",
        help="Backend to score, repeatable (default: mineru and paddleocr).",
    )
    benchmark.add_argument(
        "--include-pipeline",
        action="store_true",
        help="Also score variant C through alignment, risk assessment and conservative decisions.",
    )
    benchmark.add_argument(
        "--oracle-ceiling",
        action="store_true",
        help=(
            "Score the pipeline against replayed ground truth instead of engines: "
            "measures the pipeline's own text loss (a ceiling, not OCR accuracy)."
        ),
    )
    _add_vlm_arguments(benchmark, include_vlm_switch=True)

    return parser


def run(
    argv: Sequence[str],
    *,
    engine_factory: EngineFactory | None = None,
    benchmark_engine_factory: BenchmarkEngineFactory | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Execute a CLI invocation; injectable for deterministic tests."""

    args = build_parser().parse_args(list(argv))
    out = sys.stdout if stdout is None else stdout
    if args.command == "setup":
        return _setup(args)
    if args.command == "runtime-status":
        return _runtime_status(args, out)
    if args.command == "vlm-check":
        return _vlm_check(args, out)
    if args.command == "status":
        return _status(args, out)
    if args.command == "structure":
        return _structure(args, out)
    if args.command == "relink-source":
        return _relink_source(args, out)
    if args.command == "audit":
        return _audit_project(args, out)
    if args.command == "backup":
        return _backup_project(args, out)
    if args.command == "verify-backup":
        return _verify_project_backup(args, out)
    if args.command == "restore":
        return _restore_project_backup(args, out)
    if args.command == "review":
        return _review(args, out)
    if args.command == "resolve":
        return _resolve(args, out)
    if args.command == "confirm":
        return _confirm(args, out)
    if args.command == "export":
        return _export(args, out)
    if args.command == "export-candidates":
        return _export_candidates(args, out)
    if args.command == "ui":
        return _ui(args)
    if args.command == "benchmark":
        benchmark_factory = (
            default_benchmark_engine_factory
            if benchmark_engine_factory is None
            else benchmark_engine_factory
        )
        return _benchmark(args, out, benchmark_factory)
    try:
        reviewer = _configured_vlm_reviewer(args)
    except ValueError as exc:
        print(f"error: VLM configuration: {exc}", file=sys.stderr)
        return EXIT_FATAL

    factory = default_engine_factory if engine_factory is None else engine_factory
    if reviewer is not None:
        original_factory = factory

        def configured_factory(runtime_root: Path) -> Engines:
            return replace(original_factory(runtime_root), reviewer=reviewer)

        factory = configured_factory
    if args.command == "repair":
        return _repair(args, out, factory)
    return _process(args, out, factory)


def _preprocess_settings(args: argparse.Namespace) -> PreprocessSettings | None:
    """Translate the CLI preprocessing flags into a plan.

    Returns ``None`` when the operator disabled the stage, which keeps the raw
    scan path byte-for-byte identical to earlier versions.
    """

    if getattr(args, "no_preprocess", False):
        return None
    operations = list(default_settings().operations)
    if getattr(args, "preprocess_binarize", False):
        operations.append(PreprocessOperation.BINARIZE)
    max_skew = getattr(args, "preprocess_max_skew", None)
    return PreprocessSettings(
        operations=tuple(operations),
        max_skew_degrees=(
            default_settings().max_skew_degrees if max_skew is None else max_skew
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


def _process(args: argparse.Namespace, out: TextIO, factory: EngineFactory) -> int:
    book: Path = args.book
    if not book.is_file():
        print(f"error: book not found: {book}", file=sys.stderr)
        return EXIT_FATAL

    store_root: Path = args.store if args.store is not None else default_store_dir(book)
    layout = StoreLayout(store_root)
    runtime_root: Path = (
        args.runtime_root if args.runtime_root is not None else runtime_root_for_store(layout.root)
    )
    try:
        store = SQLiteStore(layout.db_path)
        store.initialize()
        project_id = _ensure_project(store, book)
        imported = import_source(store, project_id, open_page_source(book), layout.assets_dir)
        engines = factory(runtime_root)
        outcome = process_book(
            store,
            project_id,
            primary_engine=engines.primary,
            secondary_engine=engines.secondary,
            reviewer=engines.reviewer,
            crops_dir=layout.crops_dir,
            preprocess=_preprocess_settings(args),
            repair=_repair_settings(args),
        )
    except Exception as exc:  # noqa: BLE001 - report any setup/stage failure as a fatal exit
        message = str(exc)
        if factory is default_engine_factory and (
            "not ready" in message.lower() or "uv bootstrap tool" in message.lower()
        ):
            message += f'; run scribeforge setup --runtime-root="{runtime_root}" and retry'
        print(f"error: {message}", file=sys.stderr)
        return EXIT_FATAL

    print(
        f"imported {len(imported.imported_page_indexes)} new page(s), "
        f"{imported.page_count} total",
        file=out,
    )
    print(
        f"processed {len(outcome.pages)} page(s), {len(outcome.failed_pages)} failed",
        file=out,
    )
    # Structure is derived after OCR, from the runs that were just stored. It is
    # advisory: a failure here must not turn a successful book into a failed run,
    # because the export falls back to physical page numbering.
    try:
        derived = build_book_structure(store, project_id, engine=engines.primary.name)
        report = persist_book_structure(store, project_id, derived)
        print(f"structure: {_structure_summary(report)}", file=out)
    except Exception as exc:  # noqa: BLE001 - structure is advisory, never fatal
        print(f"structure: skipped ({exc})", file=out)
    decisions = sum(page.decision_count for page in outcome.pages)
    summary = progress(store, project_id)
    # Counted after repair rounds, not before: what matters to the operator is how
    # many pairs still need a person now, not how many needed one mid-run.
    print(
        f"recorded {decisions} decision(s), {summary.open_pairs} pair(s) need review",
        file=out,
    )
    _print_rounds(out, outcome.rounds)
    _print_progress(out, summary)
    for page_index, error in outcome.failed_pages:
        print(f"  page {page_index}: {error}", file=out)
    return EXIT_PARTIAL if outcome.failed_pages else EXIT_OK


def _structure(args: argparse.Namespace, out: TextIO) -> int:
    """Derive and persist book structure from the stored OCR evidence."""

    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL
    projects = store.list_projects()
    if not projects:
        print("error: store contains no projects", file=sys.stderr)
        return EXIT_FATAL
    project_id = projects[-1].id
    try:
        derived = build_book_structure(store, project_id, engine=args.engine)
    except KeyError:
        print(
            f"error: no stored OCR run for engine {args.engine!r}; "
            "run `scribeforge process` first or pass --engine",
            file=sys.stderr,
        )
        return EXIT_FATAL
    report = persist_book_structure(store, project_id, derived)
    if args.json:
        print(
            json.dumps(
                {
                    "pages": len(report.pages),
                    "labelled_pages": list(report.labelled_pages),
                    "chapters": report.chapters,
                    "chapter_source": report.chapter_source,
                    "roles": report.roles,
                    "blank_pages": list(report.blank_pages),
                    "plate_pages": list(report.plate_pages),
                    "unmeasured_pages": list(report.unmeasured_pages),
                    "confirmed_textless": list(report.confirmed_textless),
                    "toc_entries": report.toc_entries,
                    "toc_mapped": report.toc_mapped,
                    "toc_unmapped": list(report.toc_unmapped),
                },
                ensure_ascii=False,
            ),
            file=out,
        )
        return EXIT_OK
    print(_structure_summary(report), file=out)
    for role, count in sorted(report.roles.items()):
        print(f"  {role}: {count}", file=out)
    for title in report.toc_unmapped[:10]:
        print(f"  contents entry not anchored to a page: {title}", file=out)
    if len(report.toc_unmapped) > 10:
        print(
            f"  ... and {len(report.toc_unmapped) - 10} more unanchored contents entries",
            file=out,
        )
    return EXIT_OK


def _structure_summary(report: StructureReport) -> str:
    """One line an operator can read: where the chapter tree came from."""

    summary = (
        f"pages {len(report.pages)}, labelled {len(report.labelled_pages)}, "
        f"chapters {report.chapters} from {report.chapter_source}"
    )
    if report.toc_entries:
        summary += (
            f" (contents lists {report.toc_entries}, {report.toc_mapped} anchored)"
        )
    if report.blank_pages or report.plate_pages:
        summary += (
            f"; blank {len(report.blank_pages)}, plate {len(report.plate_pages)}"
        )
    return summary


def _print_rounds(out: TextIO, rounds: Sequence[RoundReport]) -> None:
    for report in rounds:
        print(
            f"repair round {report.round_index}: read {len(report.examined)} pair(s), "
            f"resolved {len(report.resolved)}, "
            f"still ambiguous {len(report.still_open)}, "
            f"out of attempts {len(report.skipped)}, "
            f"deferred {len(report.deferred)}",
            file=out,
        )


def _print_progress(out: TextIO, report: ProgressReport) -> None:
    print(
        f"accepted without a person: {report.accepted_automatic} automatic, "
        f"{report.accepted_vlm} by vision model, {report.accepted_human} by a human",
        file=out,
    )
    print(
        f"still open: {report.open_pairs} pair(s), "
        f"{report.open_high_risk} of them high risk, "
        f"{report.open_blocked} a machine may not accept "
        f"(auto-acceptance {report.auto_acceptance:.1%})",
        file=out,
    )


def _rounds_json(rounds: Sequence[RoundReport]) -> list[dict[str, object]]:
    return [
        {
            "round": report.round_index,
            "examined": len(report.examined),
            "resolved": len(report.resolved),
            "still_open": len(report.still_open),
            "skipped": len(report.skipped),
            "deferred": len(report.deferred),
        }
        for report in rounds
    ]


def _repair(args: argparse.Namespace, out: TextIO, factory: EngineFactory) -> int:
    """Re-read the disputed lines of an already-processed store."""

    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL
    projects = store.list_projects()
    if not projects:
        print("error: store contains no projects", file=sys.stderr)
        return EXIT_FATAL
    project_id = projects[-1].id
    layout = StoreLayout(args.store)
    runtime_root: Path = (
        args.runtime_root
        if args.runtime_root is not None
        else runtime_root_for_store(layout.root)
    )
    settings = _repair_settings(args) or RepairSettings(max_rounds=1)
    try:
        engines = factory(runtime_root)
        reports = repair_book(
            store,
            project_id,
            readers=(reader_for(engines.primary), reader_for(engines.secondary)),
            crops_dir=layout.crops_dir / "repair",
            reviewer=engines.reviewer,
            settings=settings,
        )
    except Exception as exc:  # noqa: BLE001 - report any engine/stage failure as fatal
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL

    summary = progress(store, project_id)
    if args.json:
        print(
            json.dumps(
                {"rounds": _rounds_json(reports), "progress": _progress_json(summary)},
                ensure_ascii=False,
            ),
            file=out,
        )
    else:
        if not reports:
            print("repair: nothing left to re-read", file=out)
        _print_rounds(out, reports)
        _print_progress(out, summary)
    return EXIT_OK if not summary.needs_review else EXIT_PARTIAL


def _progress_json(report: ProgressReport) -> dict[str, object]:
    return {
        "pages": report.pages,
        "pairs": report.pairs,
        "accepted_automatic": report.accepted_automatic,
        "accepted_vlm": report.accepted_vlm,
        "accepted_human": report.accepted_human,
        "open_pairs": report.open_pairs,
        "open_high_risk": report.open_high_risk,
        "open_blocked": report.open_blocked,
        "auto_acceptance": round(report.auto_acceptance, 6),
    }


def _status(args: argparse.Namespace, out: TextIO) -> int:
    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL

    projects = store.list_projects()
    if args.json:
        summaries: list[dict[str, object]] = []
        for project in projects:
            pages = store.list_pages(project.id)
            jobs = store.list_jobs(project.id)
            state_counts = {
                state: sum(job.state.value == state for job in jobs)
                for state in ("pending", "running", "succeeded", "failed")
            }
            successful_pages: dict[str, set[int]] = {}
            for job in jobs:
                if job.page_index is not None and job.state.value == "succeeded":
                    successful_pages.setdefault(job.stage.value, set()).add(job.page_index)
            dual_ocr_pages = (
                successful_pages.get("primary_ocr", set())
                & successful_pages.get("secondary_ocr", set())
            )
            failed_jobs = [
                {
                    "stage": job.stage.value,
                    "page_index": job.page_index,
                    "attempts": job.attempts,
                    "last_error": job.last_error,
                }
                for job in reversed(jobs)
                if job.state.value == "failed"
            ][:20]
            summaries.append(
                {
                    "id": project.id,
                    "title": project.title,
                    "source_uri": project.source_uri,
                    "source_sha256": project.source_sha256,
                    "pages_total": len(pages),
                    "pages_with_both_ocr_success": len(dual_ocr_pages),
                    "review_queued": len(review_queue(store, project.id)),
                    "job_counts": state_counts,
                    "failed_jobs": failed_jobs,
                }
            )
        print(
            json.dumps(
                {"schema_version": 1, "store": str(args.store), "projects": summaries},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=out,
        )
        return EXIT_OK

    if not projects:
        print("no projects", file=out)
        return EXIT_OK

    for project in projects:
        pages = store.list_pages(project.id)
        print(f"project {project.id}: {project.title} ({project.source_uri})", file=out)
        print(f"  pages: {len(pages)}", file=out)
        print(f"  review queued: {len(review_queue(store, project.id))}", file=out)
        for job in store.list_jobs(project.id):
            scope = "project" if job.page_index is None else f"page {job.page_index}"
            print(
                f"  {job.stage.value} [{scope}] {job.state.value} attempts={job.attempts}",
                file=out,
            )
    return EXIT_OK


def _relink_source(args: argparse.Namespace, out: TextIO) -> int:
    """Relink a moved source only when its bytes match the project's recorded identity."""
    source_path: Path = args.source.resolve()
    if not source_path.is_file():
        print(f"error: source file not found: {source_path}", file=sys.stderr)
        return EXIT_FATAL

    try:
        store = _open_store(args.store)
        if store is None:
            print(
                f"error: no store found at {StoreLayout(args.store).db_path}",
                file=sys.stderr,
            )
            return EXIT_FATAL

        projects = store.list_projects()
        if len(projects) != 1:
            detail = (
                "no project found"
                if not projects
                else "store contains multiple projects; select a dedicated project store"
            )
            print(f"error: cannot relink source: {detail}", file=sys.stderr)
            return EXIT_FATAL

        project = projects[0]
        source_sha256 = _file_sha256(source_path)
        if source_sha256 != project.source_sha256:
            print(
                "error: cannot relink source because the selected file's SHA-256 "
                "does not match the project's recorded source. The project path and "
                "existing processing evidence were not modified; use a new project "
                "store for changed source content.",
                file=sys.stderr,
            )
            return EXIT_FATAL

        store.relink_project_source(
            project.id,
            str(source_path),
            expected_sha256=source_sha256,
        )
    except Exception as exc:  # noqa: BLE001 - report storage and I/O errors as a fatal CLI result
        print(f"error: could not relink source: {exc}", file=sys.stderr)
        return EXIT_FATAL

    print(
        f"relinked source path for project {project.id}: {project.source_uri} -> {source_path}; "
        "SHA-256 unchanged and existing processing evidence preserved",
        file=out,
    )
    return EXIT_OK


def _audit_project(args: argparse.Namespace, out: TextIO) -> int:
    report = audit_project_store(args.store)
    payload = report.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=out)
    else:
        state = "healthy" if report.healthy else "findings detected"
        print(f"Project audit: {state}", file=out)
        print(
            f"projects={report.project_count} pages={report.page_count} "
            f"sqlite={report.sqlite_integrity} foreign_key_violations="
            f"{report.foreign_key_violations}",
            file=out,
        )
        if not report.findings:
            print("No issues found.", file=out)
        for finding in report.findings:
            location = f" ({finding.path})" if finding.path else ""
            print(
                f"[{finding.severity}] {finding.code}: {finding.message}{location}",
                file=out,
            )

    if any(finding.severity == "fatal" for finding in report.findings):
        return EXIT_FATAL
    return EXIT_PARTIAL if report.findings else EXIT_OK


def _backup_project(args: argparse.Namespace, out: TextIO) -> int:
    try:
        report = create_project_backup(args.store, args.out, overwrite=args.overwrite)
    except (ProjectBackupError, FileExistsError, OSError) as exc:
        print(f"error: could not create project backup: {exc}", file=sys.stderr)
        return EXIT_FATAL
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    else:
        print(
            f"created verified backup {report['archive']} "
            f"({report['file_count']} files, {report['total_bytes']} bytes)",
            file=out,
        )
    return EXIT_OK


def _verify_project_backup(args: argparse.Namespace, out: TextIO) -> int:
    try:
        report = verify_project_backup(args.backup)
    except (ProjectBackupError, OSError) as exc:
        print(f"error: backup verification failed: {exc}", file=sys.stderr)
        return EXIT_FATAL
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    else:
        print(
            f"verified backup {report['archive']}: {report['file_count']} files, "
            f"{report['total_bytes']} bytes; SQLite {report['sqlite_integrity']}",
            file=out,
        )
        for warning in report.get("audit_warnings", []):
            if isinstance(warning, dict):
                print(f"[warning] {warning.get('code')}: {warning.get('message')}", file=out)
    return EXIT_OK


def _restore_project_backup(args: argparse.Namespace, out: TextIO) -> int:
    try:
        report = restore_project_backup(args.backup, args.to)
    except (ProjectBackupError, FileExistsError, OSError) as exc:
        print(f"error: could not restore project backup: {exc}", file=sys.stderr)
        return EXIT_FATAL
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    else:
        print(
            f"restored verified backup to {report['store']} "
            f"({report['project_count']} projects, {report['page_count']} pages)",
            file=out,
        )
        for warning in report.get("audit_warnings", []):
            if isinstance(warning, dict):
                print(f"[warning] {warning.get('code')}: {warning.get('message')}", file=out)
    return EXIT_OK if report["healthy"] else EXIT_PARTIAL


def _review(args: argparse.Namespace, out: TextIO) -> int:
    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL

    projects = store.list_projects()
    if not projects:
        print("no projects", file=out)
        return EXIT_OK

    layout = StoreLayout(args.store)
    for project in projects:
        items = review_queue(store, project.id)
        print(f"project {project.id}: {project.title}", file=out)
        print(f"review queued: {len(items)}", file=out)
        for item in items:
            detail = review_detail(
                store,
                project.id,
                item.page_index,
                item.pair_index,
                crops_dir=layout.crops_dir,
            )
            reasons = ",".join(reason.value for reason in detail.reasons)
            print(
                f"  page {detail.page_index} pair {detail.pair_index} "
                f"score={detail.score:.2f} reasons={reasons}",
                file=out,
            )
            print(f"    primary={_render_text(detail.primary_text)}", file=out)
            print(f"    secondary={_render_text(detail.secondary_text)}", file=out)
            if detail.vlm_text is None:
                print("    vlm=none", file=out)
            else:
                uncertain = "true" if detail.vlm_uncertain else "false"
                print(f'    vlm="{detail.vlm_text}" uncertain={uncertain}', file=out)
            print(f"    crop={detail.crop_path or 'none'}", file=out)
    return EXIT_OK


def _resolve(args: argparse.Namespace, out: TextIO) -> int:
    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL

    projects = store.list_projects()
    if not projects:
        print("error: no projects in store", file=sys.stderr)
        return EXIT_FATAL

    try:
        decision_id = record_human_decision(
            store, projects[0].id, args.page, args.pair, args.text
        )
    except KeyError as exc:
        message = exc.args[0] if exc.args else exc
        print(f"error: {message}", file=sys.stderr)
        return EXIT_FATAL

    print(
        f"recorded human decision #{decision_id} for page {args.page} pair {args.pair}",
        file=out,
    )
    return EXIT_OK


def _confirm(args: argparse.Namespace, out: TextIO) -> int:
    """Record that a page has no text, so a textless page is not a guess.

    Deliberately not automatic: the whole point is that a person (or a measured
    ink ratio, in a later stage) states this on the record. ``--reason`` is
    required for the same reason.
    """

    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL

    projects = store.list_projects()
    if not projects:
        print("error: no projects in store", file=sys.stderr)
        return EXIT_FATAL

    project = projects[0]
    reason = args.reason.strip()
    if not reason:
        print("error: --reason must not be empty", file=sys.stderr)
        return EXIT_FATAL
    page_indexes = {page.page_index for page in store.list_pages(project.id)}
    if args.page not in page_indexes:
        print(f"error: no page {args.page} in project {project.id}", file=sys.stderr)
        return EXIT_FATAL

    confirmation_id = store.record_page_confirmation(
        project_id=project.id,
        page_index=args.page,
        kind=args.kind,
        actor="human",
        reason=reason,
    )
    print(
        f"confirmed page {args.page} as {args.kind} "
        f"(confirmation #{confirmation_id}): {reason}",
        file=out,
    )
    return EXIT_OK


def _report_blocked_export(exc: UnresolvedContentError, out: TextIO) -> None:
    """Say exactly what is missing, and what command clears each kind."""

    print("error: cannot export unresolved content:", file=out)
    if exc.unresolved_pairs:
        print(f"  {len(exc.unresolved_pairs)} unresolved pair(s):", file=out)
        for item in exc.unresolved_pairs[:10]:
            detail = f"    page {item.page_index} pair {item.pair_index}"
            if item.blockers:
                detail += f": {'+'.join(item.blockers)}"
            if item.characters:
                detail += f" ({', '.join(item.characters)})"
            print(detail, file=out)
        if len(exc.unresolved_pairs) > 10:
            print(f"    ... and {len(exc.unresolved_pairs) - 10} more", file=out)
        print("    inspect with 'scribeforge review'", file=out)
        print("    decide with 'scribeforge resolve --page N --pair M --text ...'", file=out)
    if exc.unverified_pages:
        print(f"  {len(exc.unverified_pages)} unverified page(s):", file=out)
        print("    " + ", ".join(str(index) for index in exc.unverified_pages[:20]), file=out)
        print("    re-run the pipeline for these pages", file=out)
    if exc.unconfirmed_pages:
        # A textless page is exportable only once somebody says so on the record.
        print(
            f"  {len(exc.unconfirmed_pages)} page(s) with no text at all, "
            "not yet confirmed as textless:",
            file=out,
        )
        for index in exc.unconfirmed_pages[:20]:
            ink = exc.measured_ink.get(index)
            if ink is None:
                print(f"    page {index}: no ink measurement available", file=out)
            else:
                # The number tells the operator which of the two problems this is.
                hint = (
                    "nothing on the page"
                    if ink < 0.01
                    else "there is content here that was not read"
                )
                print(f"    page {index}: measured ink {ink:.2%} — {hint}", file=out)
        print(
            "    if the page really has no text: "
            "'scribeforge confirm --page N --kind blank --reason ...'",
            file=out,
        )
        print(
            "    if it is a plate or illustration with no caption to read: "
            "--kind non_text",
            file=out,
        )
        print("    otherwise those pages failed to read and must be re-run", file=out)


def _export(args: argparse.Namespace, out: TextIO) -> int:
    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL

    projects = store.list_projects()
    if not projects:
        print("error: no projects in store", file=sys.stderr)
        return EXIT_FATAL

    if args.title is not None and not args.title.strip():
        print("error: title must not be empty", file=sys.stderr)
        return EXIT_FATAL
    try:
        modified = (
            _parse_modified(args.modified)
            if args.modified is not None
            else datetime.now(UTC)
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL
    author = args.author if args.author and args.author.strip() else None

    project = projects[0]
    try:
        book = assemble_verified_content(store, project.id)
    except UnresolvedContentError as exc:
        _report_blocked_export(exc, sys.stderr)
        return EXIT_FATAL

    if not book.pages:
        print("error: no pages to export", file=sys.stderr)
        return EXIT_FATAL
    summary = book.acceptance
    if args.strict_acceptance and summary.requires_human_review:
        print(
            "error: --strict-acceptance: "
            f"{len(summary.spot_check)} accepted pair(s) still need a spot check "
            f"(highest machine content risk {summary.max_machine_risk:.2f}); "
            "'scribeforge export' without the flag will still write them",
            file=sys.stderr,
        )
        return EXIT_FATAL
    if args.title is not None:
        book = replace(book, title=args.title)

    suffix = args.out.suffix.lower()
    if suffix not in (".epub", ".docx"):
        print(
            f"error: unsupported export extension {suffix or '(none)'}; use .epub or .docx",
            file=sys.stderr,
        )
        return EXIT_FATAL

    try:
        if suffix == ".epub":
            write_epub(
                book,
                args.out,
                identifier=f"urn:scribeforge:{project.source_sha256}",
                language=args.language,
                modified=modified,
                author=author,
                layout=args.layout,
            )
        else:
            write_docx(
                book,
                args.out,
                identifier=f"urn:scribeforge:{project.source_sha256}",
                language=args.language,
                modified=modified,
                author=author,
            )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL

    # DOCX is written page by page, so a layout would be a claim about a file that
    # was never re-flowed; only EPUB reports one.
    written = build_flow(book, layout=args.layout) if suffix == ".epub" else None
    if args.json:
        report = {
            "output": str(args.out),
            "pages": len(book.pages),
            "chapters": len(book.chapters),
            "confirmed_blank_pages": list(book.confirmed_blank_pages),
            "empty_pairs": len(book.empty_pairs),
            "accepted_pairs": summary.pairs,
            "machine_accepted": summary.machine_accepted,
            "human_accepted": summary.human_accepted,
            "machine_acceptance": round(summary.machine_acceptance, 4),
            "max_machine_content_risk": round(summary.max_machine_risk, 4),
            "requires_human_review": summary.requires_human_review,
            "spot_check": [
                {
                    "page": item.page_index,
                    "pair": item.pair_index,
                    "actor": item.actor,
                    "content_risk": round(item.score, 4),
                    "reasons": [reason.value for reason in item.reasons],
                    "note": item.note,
                }
                for item in summary.spot_check
            ],
            "dropped_furniture": len(book.dropped_furniture),
        }
        if written is not None:
            report["layout"] = written.layout
            report["documents"] = len(written.documents)
            report["paragraphs"] = len(written.paragraphs)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=out)
        return EXIT_OK

    print(f"exported {len(book.pages)} page(s) to {args.out}", file=out)
    if written is not None:
        print(
            f"layout: {written.layout} — {len(written.documents)} document(s), "
            f"{len(written.paragraphs)} paragraph(s)",
            file=out,
        )
        carried = sum(
            1 for paragraph in written.paragraphs if paragraph.end_page > paragraph.start_page
        )
        if carried:
            print(
                f"paragraphs continued across a page turn: {carried}",
                file=out,
            )
    if book.chapters:
        print(
            f"table of contents: {len(book.chapters)} chapter(s) from the book's "
            "structure",
            file=out,
        )
    print(
        f"accepted pairs: {summary.pairs} "
        f"(machine {summary.machine_accepted}, human {summary.human_accepted})",
        file=out,
    )
    if book.confirmed_blank_pages:
        print(
            "confirmed textless page(s): "
            + ", ".join(str(index) for index in book.confirmed_blank_pages),
            file=out,
        )
    if summary.requires_human_review:
        # Never let a machine-certified document look fully human-verified.
        print(
            f"warning: {len(summary.spot_check)} accepted pair(s) need a spot check "
            f"(highest machine content risk {summary.max_machine_risk:.2f})",
            file=out,
        )
    if book.dropped_furniture:
        # Say what was removed, grouped by reason: an export must never silently
        # decide that some text was not worth printing.
        reasons: dict[str, int] = {}
        for dropped in book.dropped_furniture:
            reasons[dropped.reason] = reasons.get(dropped.reason, 0) + 1
        detail = ", ".join(f"{reason} {count}" for reason, count in sorted(reasons.items()))
        print(
            f"excluded {len(book.dropped_furniture)} page-furniture line(s) ({detail})",
            file=out,
        )
    return EXIT_OK


def _export_candidates(args: argparse.Namespace, out: TextIO) -> int:
    """Export raw per-engine EPUB candidates without confusing them with verified exports."""
    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL

    projects = store.list_projects()
    if not projects:
        print("error: no projects in store", file=sys.stderr)
        return EXIT_FATAL
    selected_project = None
    if args.project_id is None:
        if len(projects) != 1:
            print(
                "error: store contains multiple projects; choose one with --project-id",
                file=sys.stderr,
            )
            return EXIT_FATAL
        selected_project = projects[0]
    else:
        selected_project = next(
            (item for item in projects if item.id == args.project_id), None
        )
        if selected_project is None:
            print(f"error: unknown project ID: {args.project_id}", file=sys.stderr)
            return EXIT_FATAL

    project = selected_project
    pages = store.list_pages(project.id)
    if not pages:
        print("error: project has no imported pages", file=sys.stderr)
        return EXIT_FATAL
    engines: list[str] = list(args.engine) if args.engine else ["mineru", "paddleocr"]
    if not engines or len(set(engines)) != len(engines):
        print("error: candidate engine names must be non-empty and unique", file=sys.stderr)
        return EXIT_FATAL
    invalid = [name for name in engines if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name)]
    if invalid:
        print(f"error: invalid candidate engine name: {invalid[0]!r}", file=sys.stderr)
        return EXIT_FATAL

    modified = datetime.now(UTC)
    candidate_payloads: list[tuple[str, bytes, dict[str, object]]] = []
    try:
        for engine in engines:
            candidate_pages: list[OcrCandidatePage] = []
            run_manifest: list[dict[str, object]] = []
            for page in pages:
                run_id = store.latest_engine_run(project.id, page.page_index, engine)
                result = store.load_ocr_run(run_id)
                candidate_pages.append(
                    OcrCandidatePage(
                        page_index=page.page_index,
                        run_id=run_id,
                        engine_version=result.engine_version,
                        lines=tuple(line.text for line in result.lines),
                    )
                )
                run_manifest.append(
                    {
                        "page_index": page.page_index,
                        "run_id": run_id,
                        "engine_version": result.engine_version,
                    }
                )
            candidate = OcrCandidateBook(
                source_title=project.title,
                engine=engine,
                pages=tuple(candidate_pages),
            )
            data = build_candidate_epub(
                candidate,
                identifier=f"urn:scribeforge:candidate:{project.source_sha256}:{engine}",
                language=args.language,
                modified=modified,
            )
            candidate_payloads.append(
                (
                    engine,
                    data,
                    {
                        "engine": engine,
                        "engine_versions": list(candidate.engine_versions),
                        "path": str(args.out_dir / f"candidate-{engine}.epub"),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "verification_status": "unverified",
                        "page_runs": run_manifest,
                    },
                )
            )
    except (KeyError, ValueError, OSError) as exc:
        print(f"error: could not build every OCR candidate: {exc}", file=sys.stderr)
        return EXIT_FATAL

    manifest = {
        "schema_version": 1,
        "project_id": project.id,
        "source_title": project.title,
        "source_uri": project.source_uri,
        "source_sha256": project.source_sha256,
        "created_at": modified.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verification_status": "unverified",
        "candidates": [item[2] for item in candidate_payloads],
    }
    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for engine, data, _item in candidate_payloads:
            from scribeforge.adapters.export_common import write_atomic

            write_atomic(args.out_dir / f"candidate-{engine}.epub", data)
        manifest_path = args.out_dir / "candidate-manifest.json"
        temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(manifest_path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        print(f"error: cannot write candidate exports: {exc}", file=sys.stderr)
        return EXIT_FATAL

    for engine, _data, item in candidate_payloads:
        print(f"exported unverified {engine} candidate to {item['path']}", file=out)
    print(f"wrote candidate manifest to {args.out_dir / 'candidate-manifest.json'}", file=out)
    return EXIT_OK


def _parse_modified(value: str) -> datetime:
    text = value.strip()
    if text.endswith(("z", "Z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid ISO 8601 timestamp: {value!r}") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _benchmark(
    args: argparse.Namespace, out: TextIO, factory: BenchmarkEngineFactory
) -> int:
    try:
        reviewer = _configured_vlm_reviewer(args, benchmark=True)
    except ValueError as exc:
        print(f"error: VLM configuration: {exc}", file=sys.stderr)
        return EXIT_FATAL

    manifest_path: Path = args.manifest
    if not manifest_path.is_file():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return EXIT_FATAL
    try:
        manifest = load_manifest(manifest_path)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"error: invalid manifest: {exc}", file=sys.stderr)
        return EXIT_FATAL

    root: Path = args.root if args.root is not None else manifest_path.parent
    problems = validate_corpus(manifest, root)
    if problems:
        for problem in problems:
            print(f"error: case {problem.case_id}: {problem.problem}", file=sys.stderr)
        return EXIT_FATAL

    if args.oracle_ceiling:
        return _oracle_ceiling(args, out, manifest, root, manifest_path)

    backends: list[str] = list(args.engine) if args.engine else ["mineru", "paddleocr"]
    if args.include_pipeline and (
        len(backends) != 2 or set(backends) != set(_PIPELINE_BACKENDS)
    ):
        print(
            "error: --include-pipeline requires both mineru and paddleocr exactly once",
            file=sys.stderr,
        )
        return EXIT_FATAL

    unknown = [backend for backend in backends if backend not in _BENCHMARK_BACKENDS]
    if unknown:
        print(f"error: unknown benchmark engine: {unknown[0]}", file=sys.stderr)
        return EXIT_FATAL

    runtime_root: Path = (
        args.runtime_root if args.runtime_root is not None else default_runtime_root()
    )
    try:
        engines = factory(runtime_root, tuple(backends))
    except Exception as exc:  # noqa: BLE001 - a missing backend must fail as fatal
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL
    try:
        reports_list = [run_benchmark(manifest, root, engine) for engine in engines]
        if args.include_pipeline:
            engines_by_name = {engine.name: engine for engine in engines}
            missing = [
                name for name in ("mineru", "paddleocr") if name not in engines_by_name
            ]
            if missing:
                raise ValueError(
                    "benchmark factory did not provide required backend(s): "
                    + ", ".join(missing)
                )
            reports_list.append(
                run_pipeline_benchmark(
                    manifest,
                    root,
                    engines_by_name["mineru"],
                    engines_by_name["paddleocr"],
                )
            )
            if reviewer is not None:
                reports_list.append(
                    run_pipeline_benchmark(
                        manifest,
                        root,
                        engines_by_name["mineru"],
                        engines_by_name["paddleocr"],
                        reviewer=reviewer,
                    )
                )
        reports = tuple(reports_list)
    except Exception as exc:  # noqa: BLE001 - engine/pipeline failure must fail as fatal
        print(f"error: benchmark run failed: {exc}", file=sys.stderr)
        return EXIT_FATAL

    report_text = render_report(
        reports,
        corpus=str(manifest_path),
        manifest_sha256=_file_sha256(manifest_path),
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    try:
        args.out.write_text(report_text, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write report: {exc}", file=sys.stderr)
        return EXIT_FATAL

    print(
        f"wrote benchmark report for {len(reports)} variant(s) to {args.out}",
        file=out,
    )
    return EXIT_OK


def _oracle_ceiling(
    args: argparse.Namespace,
    out: TextIO,
    manifest: BenchmarkManifest,
    root: Path,
    manifest_path: Path,
) -> int:
    """Score the pipeline with recognition error forced to zero.

    Two identical oracle engines agree on every pair, so nothing is left
    unresolved and the only error left in the report is what the pipeline itself
    removes or reorders. Reported as a ceiling so it cannot be mistaken for an
    accuracy figure.
    """

    if args.engine:
        print(
            "error: --oracle-ceiling cannot be combined with --engine",
            file=sys.stderr,
        )
        return EXIT_FATAL

    primary = OracleEngine("oracle")
    secondary = OracleEngine("oracle")
    for case in manifest.cases:
        image = (root / case.image).resolve()
        text = (root / case.ground_truth).read_text(encoding="utf-8")
        direction = (
            PageDirection(case.direction)
            if case.direction is not None
            else PageDirection.HORIZONTAL_LR
        )
        primary.register(image, text, direction=direction)
        secondary.register(image, text, direction=direction)

    try:
        reports = [
            run_benchmark(manifest, root, primary),
            run_pipeline_benchmark(
                manifest, root, primary, secondary, label="oracle-ceiling"
            ),
        ]
    except Exception as exc:  # noqa: BLE001 - a harness failure must fail loudly
        print(f"error: oracle ceiling failed: {exc}", file=sys.stderr)
        return EXIT_FATAL

    report_text = render_report(
        reports,
        corpus=str(manifest_path),
        manifest_sha256=_file_sha256(manifest_path),
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    try:
        args.out.write_text(report_text, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write report: {exc}", file=sys.stderr)
        return EXIT_FATAL

    print(f"wrote oracle ceiling report to {args.out}", file=out)
    return EXIT_OK


def _vlm_check(args: argparse.Namespace, out: TextIO) -> int:
    """Run one explicit crop/image diagnostic without opening or changing a project."""
    image_path: Path = args.image
    if not image_path.is_file():
        print(f"error: VLM check image not found: {image_path}", file=sys.stderr)
        return EXIT_FATAL

    try:
        reviewer = _configured_vlm_reviewer(args)
    except ValueError as exc:
        print(f"error: VLM configuration: {exc}", file=sys.stderr)
        return EXIT_FATAL
    if reviewer is None:
        print(
            "error: VLM configuration: --vlm-endpoint and --vlm-model are required",
            file=sys.stderr,
        )
        return EXIT_FATAL

    try:
        reading = reviewer.transcribe_crop(str(image_path.resolve()), page_index=0, pair_index=0)
    except Exception as exc:  # noqa: BLE001 - diagnostics must surface adapter/server failures
        print(f"error: VLM check failed: {exc}", file=sys.stderr)
        return EXIT_FATAL

    status = "uncertain" if reading.uncertain else (
        "blank" if not reading.text.strip() else "transcribed"
    )
    report: dict[str, object] = {
        "schema_version": 1,
        "status": status,
        "image": str(image_path.resolve()),
        "model": reading.model,
        "model_version": reading.model_version,
        "text": reading.text,
        "uncertain": reading.uncertain,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    else:
        print(f"VLM check: {status}", file=out)
        print(f"Model: {reading.model} ({reading.model_version})", file=out)
        print(f"Image: {image_path.resolve()}", file=out)
        print(f"Transcription: {reading.text!r}", file=out)
        if status != "transcribed":
            print("This result is diagnostic only; it has not been accepted as project text.", file=out)

    return EXIT_OK if status == "transcribed" else EXIT_PARTIAL


def _runtime_status(args: argparse.Namespace, out: TextIO) -> int:
    """Report private runtime state without installing, repairing, or changing it."""
    from scribeforge.runtime.workflow import RuntimeSetupWorkflow

    runtime_root: Path = (
        args.runtime_root if args.runtime_root is not None else default_runtime_root()
    )
    try:
        overview = RuntimeSetupWorkflow(runtime_root).inspect()
    except Exception as exc:  # noqa: BLE001 - diagnostics must remain actionable
        print(f"error: could not inspect runtime state: {exc}", file=sys.stderr)
        return EXIT_FATAL

    layout = RuntimeLayout(runtime_root)
    profile = overview.hardware
    backends: dict[str, dict[str, object]] = {}
    for name, backend_status in (
        ("mineru", overview.mineru),
        ("paddleocr", overview.paddleocr),
    ):
        environment = layout.environment_dir(name)
        backends[name] = {
            "state": backend_status.state,
            "failed_step": backend_status.failed_step,
            "environment_path": str(environment),
            "environment_exists": environment.is_dir(),
        }

    paths = {
        "models": str(layout.models_dir),
        "models_exists": layout.models_dir.is_dir(),
        "cache": str(layout.cache_dir),
        "cache_exists": layout.cache_dir.is_dir(),
        "tools": str(layout.tools_dir),
        "tools_exists": layout.tools_dir.is_dir(),
    }
    hardware = {
        "os": profile.os_name,
        "architecture": profile.architecture,
        "gpu_name": profile.gpu_name,
        "nvidia_driver": profile.nvidia_driver,
        "vram_mb": profile.vram_mb,
        "ram_mb": profile.ram_mb,
    }
    report = {
        "schema_version": 1,
        "runtime_root": str(runtime_root),
        "runtime_root_exists": runtime_root.is_dir(),
        "uv_available": overview.uv_available,
        "hardware": hardware,
        "paths": paths,
        "backends": backends,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True), file=out)
    else:
        print(f"Runtime root: {runtime_root}", file=out)
        print(f"uv: {'available' if overview.uv_available else 'missing'}", file=out)
        gpu = profile.gpu_name or "not detected"
        print(f"GPU: {gpu}", file=out)
        print(
            f"VRAM: {profile.vram_mb} MiB" if profile.vram_mb is not None else "VRAM: unknown",
            file=out,
        )
        print(
            f"RAM: {profile.ram_mb} MiB" if profile.ram_mb is not None else "RAM: unknown",
            file=out,
        )
        for name, backend_report in backends.items():
            failed_step = backend_report["failed_step"]
            suffix = f" (failed step: {failed_step})" if failed_step else ""
            print(f"{name}: {backend_report['state']}{suffix}", file=out)
        print(f"Models: {paths['models']}", file=out)
        print(f"Cache: {paths['cache']}", file=out)
    return EXIT_OK


def _setup(args: argparse.Namespace) -> int:
    try:
        from scribeforge.ui.runtime_window import run_runtime_setup
    except ImportError as exc:
        print(f"error: the runtime setup wizard needs Tkinter ({exc})", file=sys.stderr)
        return EXIT_FATAL

    runtime_root: Path = (
        args.runtime_root if args.runtime_root is not None else default_runtime_root()
    )
    return run_runtime_setup(runtime_root)


def _ui(args: argparse.Namespace) -> int:
    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL

    from scribeforge.ui.app import run_ui

    return run_ui(store, crops_dir=StoreLayout(args.store).crops_dir)


def _open_store(root: Path) -> SQLiteStore | None:
    layout = StoreLayout(root)
    if not layout.db_path.exists():
        return None
    store = SQLiteStore(layout.db_path)
    store.initialize()
    return store


def _render_text(text: str | None) -> str:
    return "none" if text is None else f'"{text}"'


def _ensure_project(store: SQLiteStore, book: Path) -> int:
    source_path = book.resolve()
    source_sha256 = _file_sha256(source_path)
    existing = store.list_projects()
    if not existing:
        return store.create_project(book.stem, str(source_path), source_sha256)
    if len(existing) != 1:
        raise ValueError(
            "project store contains multiple projects, but processing requires a dedicated "
            "single-project store; select the correct project store or create a new one"
        )

    project = existing[0]
    recorded_path = Path(project.source_uri).resolve()
    path_matches = recorded_path == source_path
    hash_matches = project.source_sha256 == source_sha256
    if not path_matches or not hash_matches:
        mismatch: list[str] = []
        if not path_matches:
            mismatch.append("source path differs")
        if not hash_matches:
            mismatch.append("source SHA-256 differs")
        raise ValueError(
            "source mismatch for existing project store ("
            + " and ".join(mismatch)
            + f"). Recorded source: {project.source_uri}; requested source: {source_path}. "
            "Use the original source PDF or choose a new --store directory. "
            "Existing processing evidence was not modified."
        )
    return project.id


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_engine_factory(runtime_root: Path) -> Engines:
    """Build OCR engines bound to application-managed backend runtimes.

    The backends must already be installed; a missing runtime fails fast with a
    ``BackendNotReadyError`` rather than silently degrading evidence. A VLM
    adjudicator is not wired yet, so the review stage is left for a later run.
    """

    layout = RuntimeLayout(root=runtime_root)
    manager = RuntimeManager(runtime_root)
    os_name = platform.system().lower()

    def status(backend: str) -> str:
        return manager.status(backend).state

    primary = build_runtime_engine(
        layout,
        "mineru",
        _worker_script("mineru_worker.py"),
        os_name,
        name="mineru",
        status=status,
    )
    secondary = build_runtime_engine(
        layout,
        "paddleocr",
        _worker_script("paddle_worker.py"),
        os_name,
        name="paddleocr",
        status=status,
    )
    return Engines(primary=primary, secondary=secondary, reviewer=None)


def default_benchmark_engine_factory(
    runtime_root: Path, backends: Sequence[str]
) -> tuple[OCREngine, ...]:
    """Build only the requested benchmark backends from managed runtimes.

    Benchmarking MinerU alone must not require a PaddleOCR install, so this
    factory never constructs a backend that was not asked for.
    """

    unknown = [backend for backend in backends if backend not in _BENCHMARK_BACKENDS]
    if unknown:
        raise ValueError(f"unknown benchmark engine: {unknown[0]}")

    layout = RuntimeLayout(root=runtime_root)
    manager = RuntimeManager(runtime_root)
    os_name = platform.system().lower()

    def status(backend: str) -> str:
        return manager.status(backend).state

    return tuple(
        build_runtime_engine(
            layout,
            backend,
            _worker_script(_BENCHMARK_BACKENDS[backend]),
            os_name,
            name=backend,
            status=status,
        )
        for backend in backends
    )


def _worker_script(name: str) -> Path:
    return Path(__file__).resolve().parent / "workers" / name


if __name__ == "__main__":
    raise SystemExit(main())
