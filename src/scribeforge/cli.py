from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from typing import Any, TextIO, cast

from scribeforge import report_comparison
from scribeforge.adapters.candidate_epub import build_candidate_epub
from scribeforge.adapters.docx import build_docx
from scribeforge.adapters.epub import build_epub
from scribeforge.adapters.export_common import write_atomic
from scribeforge.adapters.openai_compatible_vlm import OpenAICompatibleVLMReviewer
from scribeforge.adapters.opencc_text import opencc_simplifier
from scribeforge.adapters.runtime_workers import build_runtime_engine, worker_source_root
from scribeforge.adapters.sources import open_page_source
from scribeforge.adapters.subprocess_ocr import DEFAULT_WORKER_TIMEOUT_SECONDS
from scribeforge.benchmark.corpus import (
    CorpusFingerprint,
    fingerprint_corpus,
    validate_corpus,
)
from scribeforge.benchmark.manifest import BenchmarkManifest, load_manifest
from scribeforge.benchmark.metrics import scoring_text
from scribeforge.benchmark.oracle import OracleEngine
from scribeforge.benchmark.pipeline_runner import run_pipeline_benchmark
from scribeforge.benchmark.report import render_report, render_report_json
from scribeforge.benchmark.runner import run_benchmark
from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    cancellation_scope,
    raise_if_cancelled,
)
from scribeforge.domain.candidate import OcrCandidateBook, OcrCandidatePage
from scribeforge.domain.export_normalization import (
    NormalizationPolicy,
    derive_normalized_book,
    normalization_report_json,
)
from scribeforge.domain.flow import build_flow
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.ocr import BoundingBox, OCREngine, PageDirection
from scribeforge.domain.page_asset import PageAssetError
from scribeforge.domain.preprocess import (
    PreprocessOperation,
    PreprocessSettings,
    default_settings,
)
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.export_readiness import (
    ReadinessOptions,
    read_export_readiness,
    strict_acceptance_blocks,
)
from scribeforge.export_receipt import (
    FileFingerprint,
    make_export_receipt,
    receipt_path,
    verify_export_artifact,
)
from scribeforge.observability import (
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_LEVEL,
    LOG_FORMATS,
    LOG_LEVELS,
    configure_logging,
    get_logger,
)
from scribeforge.pipeline.adjudication import record_human_decision, review_detail, review_queue
from scribeforge.pipeline.assembly import (
    PageImageError,
    UnresolvedContentError,
    assemble_verified_content,
)
from scribeforge.pipeline.crop_evidence import verified_vlm_crop
from scribeforge.pipeline.events import EventLog
from scribeforge.pipeline.importing import import_source
from scribeforge.pipeline.ocr_frame import verification_frame
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.pipeline.rounds import (
    ProgressReport,
    RepairSettings,
    RoundReport,
    progress,
    reader_for,
    repair_book,
)
from scribeforge.pipeline.source_snapshot import verified_source_snapshot
from scribeforge.pipeline.structure import (
    StructureReport,
    build_book_structure,
    persist_book_structure,
)
from scribeforge.pipeline.textless_review import submit_textless_confirmation, textless_preview
from scribeforge.processing_report import (
    build_processing_report,
    processing_report_json,
    render_processing_report,
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
from scribeforge.storage.evidence_search import (
    ACTORS as SEARCH_ACTORS,
)
from scribeforge.storage.evidence_search import SOURCES, EvidenceSearchQuery, read_evidence_search
from scribeforge.storage.ocr_evidence import SECTIONS, EvidenceQuery, read_ocr_evidence
from scribeforge.storage.review_history import ACTORS, HistoryQuery, read_review_history
from scribeforge.storage.sqlite import DecisionAlreadyExists, JobAlreadyRunningError, SQLiteStore

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_FATAL = 2
EXIT_CANCELLED = 130
_CANCEL_CONTROL_SCHEMA_VERSION = 1


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number of seconds") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return seconds


def _add_include_page_furniture_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--include-page-furniture",
        action="store_true",
        help="Include detected headers, footers and folios in review, repair, and verified export.",
    )


def _add_worker_timeout_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--worker-timeout",
        type=_positive_seconds,
        default=DEFAULT_WORKER_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help=(
            "Maximum runtime for one OCR page worker (default: "
            f"{DEFAULT_WORKER_TIMEOUT_SECONDS:g} seconds)."
        ),
    )


def _start_cancel_control(token: CancellationToken) -> None:
    """Listen for versioned JSON control lines on stdin without blocking the CLI."""

    def listen() -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                isinstance(message, dict)
                and message.get("schema_version") == _CANCEL_CONTROL_SCHEMA_VERSION
                and message.get("type") == "cancel"
            ):
                token.cancel()
                return

    Thread(target=listen, name="scribeforge-cancel-control", daemon=True).start()


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
    parser.add_argument(
        "--log-level",
        choices=sorted(LOG_LEVELS),
        default=DEFAULT_LOG_LEVEL,
        help=f"Logging verbosity (default: {DEFAULT_LOG_LEVEL}).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="Also write logs to this rotating file; home-directory paths are redacted.",
    )
    parser.add_argument(
        "--log-format",
        choices=sorted(LOG_FORMATS),
        default=DEFAULT_LOG_FORMAT,
        help=(
            "Log line format: 'text' (default) or 'json' (one JSON object per line "
            "with ts/level/logger/run/message; issue #69)."
        ),
    )
    parser.add_argument("--location-profile", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--model-location", type=Path, help="Reuse an existing managed model layout."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    location_check = subparsers.add_parser(
        "runtime-location-check", help="Check a candidate runtime location without installing."
    )
    location_check.add_argument("--runtime-root", type=Path)

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
    _add_worker_timeout_argument(process)
    process.add_argument(
        "--workers",
        type=int,
        default=0,
        help=(
            "Pages processed concurrently (default: 0 = half the CPU cores, "
            "1 = serial). Pages are independent and the OCR engines are "
            "subprocesses, so page-level parallelism scales with I/O."
        ),
    )
    process.add_argument(
        "--page-index",
        type=int,
        action="append",
        default=None,
        help=(
            "Process only this zero-based physical page index; repeat for multiple pages. "
            "Requires an existing fully imported project; succeeded stages remain unchanged."
        ),
    )
    process.add_argument(
        "--events-file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Write structured JSONL pipeline events (book_started, page_started, "
            "page_finished, page_failed, book_finished/aborted) to this file."
        ),
    )
    process.add_argument(
        "--gpu-memory-mib",
        type=int,
        default=None,
        help=(
            "Total GPU memory budget in MiB for page-level parallelism "
            "(default: unlimited, sized by --workers only). Each in-flight page "
            "costs the sum of its engines' declared VRAM (or a conservative "
            "default); the worker count is capped so the pages fit the budget."
        ),
    )
    process.add_argument(
        "--cancelable",
        action="store_true",
        help="Read versioned JSON control messages on stdin for desktop cancellation.",
    )
    process.add_argument(
        "--reencode-format",
        type=str,
        default=None,
        choices=["jpeg", "png"],
        help=(
            "Re-encode every page raster before storing it (lossy for JPEG). "
            "Stores the re-encode parameters with the page asset so the audit "
            "always compares against what is on disk. Off by default: page "
            "rasters are kept byte-for-byte from the source render."
        ),
    )
    process.add_argument(
        "--reencode-quality",
        type=int,
        default=None,
        help="JPEG quality 1-100 when --reencode-format is jpeg (default: encoder default).",
    )
    _add_repair_arguments(process)
    _add_vlm_arguments(process)

    setup = subparsers.add_parser("setup", help="Configure private OCR runtimes and models.")
    setup.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Private runtime directory (default: the per-user ScribeForge runtime folder).",
    )
    setup.add_argument(
        "--json",
        action="store_true",
        help="Stream versioned JSON progress events for a native desktop client.",
    )
    setup.add_argument(
        "--backend",
        choices=("mineru", "paddleocr", "paddleocr-vl", "surya"),
        default=None,
        help=(
            "Repair only this OCR backend (requires --json); the optional "
            "technology-route backends paddleocr-vl and surya are accepted by name."
        ),
    )
    setup.add_argument(
        "--cancelable",
        action="store_true",
        help="Read versioned JSON cancellation messages on stdin during setup.",
    )
    setup.add_argument(
        "--step-timeout",
        type=_positive_seconds,
        default=None,
        metavar="SECONDS",
        help=(
            "Override the overall deadline for each setup step. By default, "
            "package installation, model downloads and health checks use "
            "separate runtime-plan deadlines."
        ),
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

    runtime_clean = subparsers.add_parser(
        "runtime-clean",
        help=(
            "Free disk space by removing disposable runtime artifacts "
            "(staging/previous environments, optionally cache and models)."
        ),
    )
    runtime_clean.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Private runtime directory (default: the per-user ScribeForge runtime folder).",
    )
    runtime_clean.add_argument(
        "--cache",
        action="store_true",
        help="Also remove the download cache (models are kept).",
    )
    runtime_clean.add_argument(
        "--models",
        action="store_true",
        help="Also remove downloaded models (they will be re-downloaded on next setup).",
    )
    runtime_clean.add_argument(
        "--json", action="store_true", help="Emit a stable JSON cleanup report."
    )

    vlm_check = subparsers.add_parser(
        "vlm-check",
        help="Test a configured VLM endpoint with an explicit image without writing project data.",
    )
    vlm_check.add_argument(
        "--image",
        type=Path,
        required=True,
        help="Image/crop to send for one diagnostic transcription.",
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

    diagnosis = subparsers.add_parser(
        "diagnose-book", help="Compare source pages, project progress, and EPUB page markers."
    )
    diagnosis.add_argument("--store", type=Path, required=True)
    diagnosis.add_argument("--source", type=Path, required=True)
    diagnosis.add_argument("--epub", type=Path, required=True)

    layout_preview = subparsers.add_parser(
        "layout-preview", help="Create an unverified preview of an original-frame OCR region."
    )
    layout_preview.add_argument("--store", type=Path, required=True)
    layout_preview.add_argument("--project", type=int, required=True, help="Project ID.")
    layout_preview.add_argument(
        "--page", type=int, required=True, help="Zero-based physical page index."
    )
    layout_preview.add_argument(
        "--run", type=int, required=True, help="Latest verification OCR run ID."
    )
    layout_preview.add_argument(
        "--block", type=int, required=True, help="Zero-based OCR block index."
    )
    layout_preview.add_argument(
        "--out", type=Path, required=True, help="New PNG preview path; never overwritten."
    )

    processing_report = subparsers.add_parser(
        "report",
        help="Render processing timing, risk and review evidence from a project store.",
    )
    processing_report.add_argument(
        "--store", type=Path, required=True, help="Project store directory."
    )
    processing_report.add_argument(
        "--project",
        type=int,
        default=None,
        help="Project ID (required only when the store contains multiple projects).",
    )
    processing_report.add_argument(
        "--json", action="store_true", help="Emit stable schema-v1 JSON instead of Markdown."
    )
    processing_report.add_argument(
        "--events",
        type=Path,
        default=None,
        metavar="PATH",
        help="Optional JSONL event log from 'process --events-file'; its summary is appended.",
    )
    processing_report.add_argument(
        "--compare",
        type=Path,
        default=None,
        metavar="OLD_JSON",
        help=(
            "Compare the current report against a previously exported schema-v1 "
            "JSON report and render the field-level deltas instead."
        ),
    )
    processing_report.add_argument(
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="How many slowest pages to list in the report (default: 10).",
    )

    events = subparsers.add_parser(
        "events",
        help="Analyze a JSONL pipeline event log (from 'process --events-file') into a bottleneck report.",
    )
    events.add_argument("file", type=Path, help="Event log JSONL file to analyze.")
    events.add_argument("--json", action="store_true", help="Emit stable JSON instead of Markdown.")
    events.add_argument(
        "--top",
        type=int,
        default=10,
        help="How many slowest pages to list (default: 10).",
    )
    events.add_argument(
        "--estimate-remaining",
        type=int,
        default=None,
        metavar="PAGES",
        help=(
            "Estimate remaining wall seconds for PAGES unprocessed pages, using "
            "the trace's observed mean page time and the run's worker count."
        ),
    )
    events.add_argument(
        "--plan",
        action="store_true",
        help="Also print a simulated wave schedule for the book's declared page count.",
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
    audit.add_argument("--json", action="store_true", help="Emit a stable JSON audit report.")

    rehash_pages = subparsers.add_parser(
        "rehash-pages",
        help="Record page asset digests for pages that were imported before schema v11.",
    )
    rehash_pages.add_argument("--store", type=Path, required=True, help="Project store directory.")
    rehash_pages.add_argument("--json", action="store_true", help="Emit a JSON rehash report.")

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
        "verify-backup", help="Verify archive, SQLite, and recorded evidence in a temporary store."
    )
    verify_backup.add_argument("--backup", type=Path, required=True, help="Backup ZIP archive.")
    verify_backup.add_argument(
        "--json", action="store_true", help="Emit a JSON verification report."
    )

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
    _add_worker_timeout_argument(repair)
    _add_vlm_arguments(repair)
    _add_include_page_furniture_argument(repair)
    repair.add_argument("--json", action="store_true", help="Emit a JSON repair report.")

    review = subparsers.add_parser(
        "review", help="List the page pairs that still need a human decision."
    )
    review.add_argument("--store", type=Path, required=True, help="Project store directory.")
    _add_include_page_furniture_argument(review)
    review.add_argument(
        "--json", action="store_true", help="Emit schema-versioned review evidence as JSON."
    )

    search = subparsers.add_parser(
        "evidence-search", help="Search saved OCR or decisions literally."
    )
    search.add_argument("--store", type=Path, required=True)
    search.add_argument("--project-id", type=int, required=True)
    search.add_argument("--source", choices=SOURCES, required=True)
    search.add_argument("--term", required=True)
    search.add_argument("--page-start", type=int, help="First physical page index (zero-based).")
    search.add_argument("--page-end", type=int, help="Last physical page index (inclusive).")
    search.add_argument("--actor", choices=SEARCH_ACTORS)
    search.add_argument("--snapshot-id", type=int)
    search.add_argument("--before-id", type=int)
    search.add_argument(
        "--record-id", type=int, help="Read a longer excerpt for a matching record."
    )
    search.add_argument("--limit", type=int, default=20)

    evidence = subparsers.add_parser("ocr-evidence", help="Browse saved OCR evidence as JSON.")
    evidence.add_argument("--store", type=Path, required=True)
    evidence.add_argument("--project-id", type=int, required=True)
    evidence.add_argument("--section", choices=SECTIONS, required=True)
    evidence.add_argument("--page", type=int, help="Zero-based physical page index.")
    evidence.add_argument("--run", type=int, help="OCR run for lines, verification run for pairs.")
    evidence.add_argument("--after", type=int, default=-1)
    evidence.add_argument("--limit", type=int, default=25)

    history = subparsers.add_parser("review-history", help="Read the append-only decision history.")
    history.add_argument("--store", type=Path, required=True)
    history.add_argument("--project-id", type=int, required=True)
    history.add_argument("--page", type=int, help="Zero-based physical page index.")
    history.add_argument("--pair", type=int, help="Zero-based aligned-pair index; requires --page.")
    history.add_argument("--actor", choices=ACTORS)
    history.add_argument("--snapshot-id", type=int, help="Upper bound from the first response.")
    history.add_argument("--before-id", type=int, help="Exclusive cursor from the prior page.")
    history.add_argument("--decision-id", type=int, help="Load a single record with extended text.")
    history.add_argument("--limit", type=int, default=30)

    resolve = subparsers.add_parser("resolve", help="Record a human decision for one page pair.")
    resolve.add_argument("--store", type=Path, required=True, help="Project store directory.")
    resolve.add_argument(
        "--project-id", type=int, default=None, help="Project ID (defaults to the first project)."
    )
    resolve.add_argument("--page", type=int, required=True, help="Zero-based page index.")
    resolve.add_argument("--pair", type=int, required=True, help="Zero-based aligned-pair index.")
    resolve.add_argument(
        "--text", required=True, help="Literal text the reviewer accepted for the pair."
    )

    resolve.add_argument(
        "--review-token",
        help="Verification and VLM version from review JSON; refuses stale evidence.",
    )

    textless = subparsers.add_parser(
        "textless-preview", help="Get checked raster and confirmation history."
    )
    textless.add_argument("--store", type=Path, required=True)
    textless.add_argument("--project-id", type=int, required=True)
    textless.add_argument("--page", type=int, required=True)

    confirm = subparsers.add_parser(
        "confirm",
        help="Record that a page carries no text (a blank leaf or a plate).",
    )
    confirm.add_argument("--store", type=Path, required=True, help="Project store directory.")
    confirm.add_argument("--project-id", type=int, help="Required with an evidence token.")
    confirm.add_argument("--confirmation-token", help="Bind the decision to a textless preview.")
    confirm.add_argument(
        "--json", action="store_true", help="Return a structured confirmation receipt."
    )
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

    ui = subparsers.add_parser("ui", help="Open the read-only desktop shell for a project store.")
    ui.add_argument("--store", type=Path, required=True, help="Project store directory.")

    comparison = subparsers.add_parser(
        "compare-processing-reports", help="Compare two saved processing report JSON files."
    )
    comparison.add_argument("--baseline", type=Path, required=True)
    comparison.add_argument("--candidate", type=Path, required=True)
    comparison.add_argument("--filter", choices=report_comparison.FILTERS, default="changed")
    comparison.add_argument("--order", choices=report_comparison.ORDERS, default="page")
    comparison.add_argument("--offset", type=int, default=0)
    comparison.add_argument("--limit", type=int, default=30)
    comparison.add_argument("--baseline-sha256")
    comparison.add_argument("--candidate-sha256")
    comparison.add_argument("--allow-unmatched", action="store_true")

    verify_export = subparsers.add_parser(
        "verify-export", help="Check the bytes of an EPUB or DOCX against local export sidecars."
    )
    verify_export.add_argument("--file", type=Path, required=True)
    verify_export.add_argument(
        "--source-sha256", help="Compare to the expected source document digest."
    )

    readiness = subparsers.add_parser(
        "export-readiness", help="Assess trusted export content without writing any output files."
    )
    readiness.add_argument("--store", type=Path, required=True)
    _add_include_page_furniture_argument(readiness)
    readiness.add_argument("--strict-acceptance", action="store_true")
    readiness.add_argument(
        "--page", type=int, help="Limit issue preview to this physical page index."
    )
    readiness.add_argument(
        "--limit", type=int, default=120, help="Maximum number of returned issues."
    )

    export = subparsers.add_parser(
        "export", help="Write the verified text of a project to EPUB 3 or DOCX."
    )
    export.add_argument("--store", type=Path, required=True, help="Project store directory.")
    _add_include_page_furniture_argument(export)
    export.add_argument("--out", type=Path, required=True, help="Destination .epub or .docx file.")
    export.add_argument("--language", default="und", help="BCP 47 language tag (default: und).")
    export.add_argument(
        "--title", default=None, help="Override the book title (default: project title)."
    )
    export.add_argument("--author", default=None, help="Book author metadata.")
    export.add_argument(
        "--modified",
        default=None,
        help=(
            "ISO 8601 export timestamp for reproducible builds "
            "(default: now; naive values are treated as timezone.utc)."
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
        "--normalization",
        choices=tuple(policy.value for policy in NormalizationPolicy),
        default=NormalizationPolicy.PRESERVE.value,
        help=(
            "Export-only text derivation (default: preserve). canonical_variants "
            "uses the explicit variant table; simplified uses OpenCC t2s. Accepted "
            "OCR decisions are never modified; a JSON change report is always written."
        ),
    )
    export.add_argument("--json", action="store_true", help="Emit a JSON export report.")

    candidates = subparsers.add_parser(
        "export-candidates",
        help="Export unverified raw OCR candidates for side-by-side EPUB comparison.",
    )
    candidates.add_argument("--store", type=Path, required=True, help="Project store directory.")
    candidates.add_argument(
        "--out-dir", type=Path, required=True, help="Directory for candidate EPUBs and manifest."
    )
    candidates.add_argument(
        "--project-id",
        type=int,
        default=None,
        help="Project ID (required when store has multiple projects).",
    )
    candidates.add_argument(
        "--engine",
        action="append",
        default=[],
        metavar="NAME",
        help="OCR engine name, repeatable (default: mineru and paddleocr).",
    )
    candidates.add_argument("--language", default="und", help="BCP 47 language tag (default: und).")

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
    benchmark.add_argument("--out", type=Path, required=True, help="Destination Markdown report.")
    benchmark.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Managed runtime root (default: <root>/runtime).",
    )
    _add_worker_timeout_argument(benchmark)
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
    benchmark.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Do not score any engine: only run the corpus quality gate (#62/#68) "
            "over the manifest and write its report to --out. Exits non-zero when "
            "the corpus has blocking issues."
        ),
    )
    benchmark.add_argument(
        "--quality-json",
        action="store_true",
        help="With --check-only, emit the gate result as JSON instead of Markdown.",
    )
    benchmark.add_argument(
        "--annotation-status",
        action="store_true",
        help=(
            "Do not score any engine: write a corpus annotation-status report "
            "(per-language/direction coverage, empty ground truths, gate verdict) "
            "to --out. Exits 2 while annotations are incomplete."
        ),
    )
    benchmark.add_argument(
        "--report-json",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Additionally write the scored run as schema-v1 JSON (with mandatory "
            "per-language strata and vertical reading-order rates) to PATH."
        ),
    )
    benchmark.add_argument(
        "--history",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Append this scored run as one schema-v1 JSON line to a JSONL history "
            "file for 'benchmark-trend' (#62/#68/#90)."
        ),
    )

    alignment_eval = subparsers.add_parser(
        "alignment-eval",
        help=(
            "Evaluate line alignment on hand-labelled layout fixtures and report "
            "match accuracy, reading-order errors and review-queue counts (#79)."
        ),
    )
    alignment_eval.add_argument(
        "--fixtures",
        type=Path,
        required=True,
        help="Hand-labelled alignment fixture JSON (see tests/fixtures/alignment_groundtruth.json).",
    )
    alignment_eval.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination Markdown report (default: print to stdout).",
    )
    alignment_eval.add_argument(
        "--json",
        action="store_true",
        help="Print the evaluation as JSON instead of Markdown.",
    )

    benchmark_compare = subparsers.add_parser(
        "benchmark-compare",
        help=(
            "Compare two schema-v1 benchmark JSON reports (from 'benchmark "
            "--report-json') and render a regression report (#62/#68/#90)."
        ),
    )
    benchmark_compare.add_argument(
        "baseline", type=Path, help="Baseline benchmark report JSON file."
    )
    benchmark_compare.add_argument("current", type=Path, help="Current benchmark report JSON file.")
    benchmark_compare.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination file (default: stdout). Markdown unless --json is given.",
    )
    benchmark_compare.add_argument(
        "--json",
        action="store_true",
        help="Emit the comparison as schema-v1 JSON instead of Markdown.",
    )
    benchmark_compare.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 3 when a gated metric (CER/WER/reading-order rate) regresses beyond its threshold.",
    )
    benchmark_compare.add_argument(
        "--cer-pp",
        type=float,
        default=0.5,
        help="Allowed CER regression in percentage points (default: 0.5).",
    )
    benchmark_compare.add_argument(
        "--wer-pp",
        type=float,
        default=1.0,
        help="Allowed WER regression in percentage points (default: 1.0).",
    )
    benchmark_compare.add_argument(
        "--ro-pp",
        type=float,
        default=1.0,
        help="Allowed reading-order error-rate regression in percentage points (default: 1.0).",
    )

    benchmark_trend = subparsers.add_parser(
        "benchmark-trend",
        help=(
            "Render per-engine metric trends over a 'benchmark --history' JSONL "
            "file, with an optional degradation gate (#62/#68/#90)."
        ),
    )
    benchmark_trend.add_argument(
        "history", type=Path, help="JSONL history file written by 'benchmark --history'."
    )
    benchmark_trend.add_argument(
        "--engine",
        action="append",
        default=[],
        metavar="NAME",
        help="Engine to analyze, repeatable (default: every engine in the history).",
    )
    benchmark_trend.add_argument(
        "--metric",
        action="append",
        default=[],
        metavar="NAME",
        help=(
            "Metric to analyze, repeatable (default: mean_cer, mean_wer, reading_order_error_rate)."
        ),
    )
    benchmark_trend.add_argument(
        "--scope",
        default="aggregate",
        help=(
            "Series scope: aggregate, language:<code>, direction:<dir>, "
            "case:<id> or vertical_reading_order (default: aggregate)."
        ),
    )
    benchmark_trend.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Destination file (default: stdout). Markdown unless --json is given.",
    )
    benchmark_trend.add_argument(
        "--json",
        action="store_true",
        help="Emit the trend as schema-v1 JSON instead of Markdown.",
    )
    benchmark_trend.add_argument(
        "--fail-on-trend",
        action="store_true",
        help="Exit 3 when a metric's last --window deltas are all adverse beyond --max-adverse-pp.",
    )
    benchmark_trend.add_argument(
        "--window",
        type=int,
        default=3,
        help="Consecutive adverse deltas required for a degrading verdict (default: 3).",
    )
    benchmark_trend.add_argument(
        "--max-adverse-pp",
        type=float,
        default=1.0,
        help="Allowed adverse change over the window in percentage points (default: 1.0).",
    )

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
    from scribeforge.runtime.location import (
        LocationError,
        RuntimeLocation,
        location_report,
        model_location_scope,
    )

    check = args.command == "runtime-location-check"
    explicit = args.location_profile or args.model_location is not None
    location_commands = {
        "setup",
        "runtime-status",
        "runtime-clean",
        "process",
        "repair",
        "benchmark",
    }
    if check or explicit or args.command in location_commands:
        root = getattr(args, "runtime_root", None)
        if root is None:
            store = getattr(args, "store", None)
            root = (
                runtime_root_for_store(store)
                if args.command in {"process", "repair"} and store is not None and not explicit
                else default_runtime_root()
            )
        try:
            if check:
                report = location_report(root, args.model_location, platform.system().lower())
                print(json.dumps(report, ensure_ascii=True), file=out)
                return EXIT_OK
            location = (
                RuntimeLocation.resolve(root, args.model_location)
                if explicit
                else RuntimeLocation.from_bound_root(root)
            )
            if location is not None:
                if args.command not in location_commands:
                    raise LocationError(
                        "unsupported_command", "This command does not use a runtime location."
                    )
                args.location_profile = True
                args.runtime_root = location.root
                with model_location_scope(location.models):
                    if args.command == "setup":
                        if not args.json:
                            raise LocationError(
                                "json_required", "Location-aware setup requires --json."
                            )
                        location.check_paths()
                        with location.configuration_lock():
                            if args.backend is None:
                                report = location_report(
                                    location.root, location.models, platform.system().lower()
                                )
                                disk = cast(dict[str, object], report["disk"])
                                if disk["sufficient"] is not True:
                                    raise LocationError("disk_space", str(disk["advice"]))
                            location.claim()
                            return _dispatch(args, out, engine_factory, benchmark_engine_factory)
                    if args.command == "runtime-clean":
                        location.check_binding()
                        if args.models and location.models is not None:
                            raise LocationError(
                                "external_models",
                                "External model directories are not managed by cleanup.",
                            )
                        with location.configuration_lock():
                            return _dispatch(args, out, engine_factory, benchmark_engine_factory)
                    location.check_paths()
                    return _dispatch(args, out, engine_factory, benchmark_engine_factory)
        except (OSError, LocationError) as exc:
            if check:
                failure = {
                    "schema_version": 1,
                    "ok": False,
                    "code": exc.code if isinstance(exc, LocationError) else "io_error",
                    "message": str(exc),
                }
                print(json.dumps(failure, ensure_ascii=True), file=out)
            else:
                print(f"error: runtime location: {exc}", file=sys.stderr)
            return EXIT_FATAL
    return _dispatch(args, out, engine_factory, benchmark_engine_factory)


def _dispatch(
    args: argparse.Namespace,
    out: TextIO,
    engine_factory: EngineFactory | None,
    benchmark_engine_factory: BenchmarkEngineFactory | None,
) -> int:
    configure_logging(args.log_level, args.log_file, log_format=args.log_format)
    cli_log = get_logger("cli")
    cli_log.info("command=%s log_level=%s", args.command, args.log_level)
    if args.command == "setup":
        setup_token = CancellationToken()
        if args.cancelable:
            _start_cancel_control(setup_token)
        with cancellation_scope(setup_token):
            return _setup(args, out)
    if args.command == "runtime-status":
        return _runtime_status(args, out)
    if args.command == "runtime-clean":
        return _runtime_clean(args, out)
    if args.command == "vlm-check":
        return _vlm_check(args, out)
    if args.command == "status":
        return _status(args, out)
    if args.command == "diagnose-book":
        return _diagnose_book(args, out)
    if args.command == "layout-preview":
        return _layout_preview(args, out)
    if args.command == "report":
        return _processing_report(args, out)
    if args.command == "events":
        return _events_report(args, out)
    if args.command == "structure":
        return _structure(args, out)
    if args.command == "relink-source":
        return _relink_source(args, out)
    if args.command == "audit":
        return _audit_project(args, out)
    if args.command == "rehash-pages":
        return _rehash_pages(args, out)
    if args.command == "backup":
        return _backup_project(args, out)
    if args.command == "verify-backup":
        return _verify_project_backup(args, out)
    if args.command == "restore":
        return _restore_project_backup(args, out)
    if args.command == "review":
        return _review(args, out)
    if args.command == "evidence-search":
        return _evidence_search(args, out)
    if args.command == "ocr-evidence":
        return _ocr_evidence(args, out)
    if args.command == "review-history":
        return _review_history(args, out)
    if args.command == "resolve":
        return _resolve(args, out)
    if args.command == "textless-preview":
        return _textless_preview(args, out)
    if args.command == "confirm":
        return _confirm(args, out)
    if args.command == "compare-processing-reports":
        return _compare_processing_reports(args, out)
    if args.command == "verify-export":
        return _verify_export(args, out)
    if args.command == "export-readiness":
        return _export_readiness(args, out)
    if args.command == "export":
        return _export(args, out)
    if args.command == "export-candidates":
        return _export_candidates(args, out)
    if args.command == "ui":
        return _ui(args)
    if args.command == "benchmark":
        if getattr(args, "annotation_status", False):
            from scribeforge.benchmark.annotation import (
                annotation_status,
                annotation_status_json,
                render_annotation_status,
            )

            manifest = load_manifest(args.manifest)
            root = args.root if args.root is not None else args.manifest.parent
            status = annotation_status(root)
            if getattr(args, "quality_json", False):
                args.out.write_text(annotation_status_json(status), encoding="utf-8")
            else:
                args.out.write_text(render_annotation_status(status), encoding="utf-8")
            print(
                f"annotation status: {'COMPLETE' if status.annotation_complete else 'INCOMPLETE'} "
                f"({status.case_count} case(s), {len(status.empty_ground_truths)} empty ground truth(s)) "
                f"-> {args.out}",
                file=out,
            )
            return 2 if not status.annotation_complete else 0
        if getattr(args, "check_only", False):
            from scribeforge.benchmark.quality_gate import (
                QualityGateSettings,
                check_corpus_quality,
                quality_gate_json,
                render_quality_report,
            )

            manifest = load_manifest(args.manifest)
            settings = QualityGateSettings(check_images=True)
            root = args.root if args.root is not None else args.manifest.parent
            issues = check_corpus_quality(manifest, settings=settings, root=root)
            if getattr(args, "quality_json", False):
                args.out.write_text(quality_gate_json(manifest, issues), encoding="utf-8")
            else:
                args.out.write_text(render_quality_report(manifest, issues), encoding="utf-8")
            errors = sum(1 for issue in issues if issue.severity == "error")
            print(
                f"quality gate: {'FAIL' if errors else 'PASS'} "
                f"({len(issues)} issue(s), {errors} error(s)) -> {args.out}",
                file=out,
            )
            return 2 if errors else 0
        if benchmark_engine_factory is None:

            def build_default_benchmark_factory(
                root: Path, backends: Sequence[str]
            ) -> tuple[OCREngine, ...]:
                return default_benchmark_engine_factory(
                    root,
                    backends,
                    worker_timeout_seconds=args.worker_timeout,
                )

            benchmark_factory: BenchmarkEngineFactory = build_default_benchmark_factory
        else:
            benchmark_factory = benchmark_engine_factory
        return _benchmark(args, out, benchmark_factory)
    if args.command == "alignment-eval":
        return _alignment_eval(args, out)
    if args.command == "benchmark-compare":
        return _benchmark_compare(args, out)
    if args.command == "benchmark-trend":
        return _benchmark_trend(args, out)
    try:
        reviewer = _configured_vlm_reviewer(args)
    except ValueError as exc:
        print(f"error: VLM configuration: {exc}", file=sys.stderr)
        return EXIT_FATAL

    uses_default_engine_factory = engine_factory is None
    if engine_factory is None:

        def build_default_engine_factory(runtime_root: Path) -> Engines:
            return default_engine_factory(
                runtime_root,
                worker_timeout_seconds=args.worker_timeout,
            )

        factory: EngineFactory = build_default_engine_factory
    else:
        factory = engine_factory
    if reviewer is not None:
        original_factory = factory

        def configured_factory(runtime_root: Path) -> Engines:
            return replace(original_factory(runtime_root), reviewer=reviewer)

        factory = configured_factory

    token = CancellationToken()
    if args.command == "process" and args.cancelable:
        _start_cancel_control(token)
    with cancellation_scope(token):
        if args.command == "repair":
            return _repair(args, out, factory)
        return _process(
            args,
            out,
            factory,
            uses_default_engine_factory=uses_default_engine_factory,
        )


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
        max_skew_degrees=(default_settings().max_skew_degrees if max_skew is None else max_skew),
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run(sys.argv[1:] if argv is None else argv)


def _process(
    args: argparse.Namespace,
    out: TextIO,
    factory: EngineFactory,
    *,
    uses_default_engine_factory: bool = False,
) -> int:
    book: Path = args.book
    if not book.is_file():
        print(f"error: book not found: {book}", file=sys.stderr)
        return EXIT_FATAL

    store_root: Path = args.store if args.store is not None else default_store_dir(book)
    layout = StoreLayout(store_root)
    runtime_root: Path = (
        args.runtime_root if args.runtime_root is not None else runtime_root_for_store(layout.root)
    )
    selected_pages = getattr(args, "page_index", None)
    if selected_pages is not None:
        if not layout.db_path.is_file():
            print("error: scoped processing requires an existing project store", file=sys.stderr)
            return EXIT_FATAL
        events_path = getattr(args, "events_file", None)
        if events_path is not None and events_path.exists():
            print("error: scoped processing requires a new events file", file=sys.stderr)
            return EXIT_FATAL
        if getattr(args, "repair_rounds", 0):
            print("error: scoped processing cannot run book-wide repair rounds", file=sys.stderr)
            return EXIT_FATAL
        if any(index < 0 for index in selected_pages) or len(set(selected_pages)) != len(
            selected_pages
        ):
            print("error: page indexes must be distinct non-negative integers", file=sys.stderr)
            return EXIT_FATAL
    try:
        store = SQLiteStore(layout.db_path)
        store.initialize()
        if selected_pages is not None:
            existing = store.list_projects()
            if len(existing) != 1:
                raise ValueError("scoped processing requires a single existing project")
            existing_id = existing[0].id
            jobs = store.list_jobs(existing_id)
            if not any(
                job.stage is JobStage.IMPORT and job.state is JobState.SUCCEEDED for job in jobs
            ):
                raise ValueError("scoped processing requires a completed import")
            available = {page.page_index for page in store.list_pages(existing_id)}
            missing = set(selected_pages) - available
            if missing:
                raise ValueError(f"selected physical page indexes not imported: {sorted(missing)}")
        project_id = _ensure_project(store, book)
        reencode = None
        fmt = getattr(args, "reencode_format", None)
        if fmt is not None:
            reencode = {"format": fmt}
            quality = getattr(args, "reencode_quality", None)
            if quality is not None:
                reencode["quality"] = quality
        project = next(project for project in store.list_projects() if project.id == project_id)
        import_started = False
        try:
            with verified_source_snapshot(book, project.source_sha256) as snapshot:
                source = open_page_source(snapshot)
                import_started = True
                imported = import_source(
                    store,
                    project_id,
                    source,
                    layout.assets_dir,
                    reencode=reencode,
                )
        except ProcessingCancelled as exc:
            if not import_started:
                job_id = store.ensure_job(project_id, JobStage.IMPORT)
                if store.load_job(job_id).state is not JobState.SUCCEEDED:
                    try:
                        store.start_job(job_id)
                    except JobAlreadyRunningError:
                        # Never alter a job owned by another process.
                        pass
                    else:
                        store.fail_job(job_id, str(exc).strip() or "processing cancelled")
            raise
        raise_if_cancelled()
        engines = factory(runtime_root)
        gpu_memory_mib = getattr(args, "gpu_memory_mib", None)
        if gpu_memory_mib is not None:
            from scribeforge.domain.scheduling import (
                format_worker_plan,
                per_page_vram_mib,
                resolve_cpu_workers,
            )

            print(
                format_worker_plan(
                    resolve_cpu_workers(getattr(args, "workers", 0)),
                    memory_budget_mib=gpu_memory_mib,
                    per_page_vram_mib=per_page_vram_mib((engines.primary, engines.secondary)),
                ),
                file=out,
            )
        events_path = getattr(args, "events_file", None)
        event_log = EventLog(events_path) if events_path is not None else None
        try:
            outcome = process_book(
                store,
                project_id,
                primary_engine=engines.primary,
                secondary_engine=engines.secondary,
                reviewer=engines.reviewer,
                crops_dir=layout.crops_dir,
                preprocess=_preprocess_settings(args),
                repair=_repair_settings(args),
                max_workers=getattr(args, "workers", 0),
                gpu_memory_mib=gpu_memory_mib,
                event_log=event_log,
                page_indexes=selected_pages,
            )
        finally:
            if event_log is not None:
                event_log.close()
    except ProcessingCancelled as exc:
        print(f"processing cancelled: {exc}", file=out)
        return EXIT_CANCELLED
    except Exception as exc:  # noqa: BLE001 - report any setup/stage failure as a fatal exit
        message = str(exc)
        if uses_default_engine_factory and (
            "not ready" in message.lower() or "uv bootstrap tool" in message.lower()
        ):
            message += f'; run scribeforge setup --runtime-root="{runtime_root}" and retry'
        print(f"error: {message}", file=sys.stderr)
        return EXIT_FATAL

    print(
        f"imported {len(imported.imported_page_indexes)} new page(s), {imported.page_count} total",
        file=out,
    )
    print(
        f"processed {len(outcome.pages)} page(s), {len(outcome.failed_pages)} failed",
        file=out,
    )
    if selected_pages is not None:
        print("structure: unchanged for scoped processing; rebuild separately if needed", file=out)
    else:
        # Structure is derived after OCR, from the runs that were just stored. It is
        # advisory: a failure here must not turn a successful book into a failed run,
        # because the export falls back to physical page numbering.
        try:
            raise_if_cancelled()
            derived = build_book_structure(store, project_id, engine=engines.primary.name)
            raise_if_cancelled()
            report = persist_book_structure(store, project_id, derived)
            print(f"structure: {_structure_summary(report)}", file=out)
        except ProcessingCancelled as exc:
            print(f"processing cancelled: {exc}", file=out)
            return EXIT_CANCELLED
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
        summary += f" (contents lists {report.toc_entries}, {report.toc_mapped} anchored)"
    if report.blank_pages or report.plate_pages:
        summary += f"; blank {len(report.blank_pages)}, plate {len(report.plate_pages)}"
    return summary


def _print_rounds(out: TextIO, rounds: Sequence[RoundReport]) -> None:
    for report in rounds:
        print(
            f"repair round {report.round_index}: read {len(report.examined)} pair(s), "
            f"resolved {len(report.resolved)}, "
            f"still ambiguous {len(report.still_open)}, "
            f"out of attempts {len(report.skipped)}, "
            f"deferred {len(report.deferred)}, "
            f"decided elsewhere {len(report.already_decided)}",
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
            "already_decided": len(report.already_decided),
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
        args.runtime_root if args.runtime_root is not None else runtime_root_for_store(layout.root)
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
            include_page_furniture=args.include_page_furniture,
        )
    except Exception as exc:  # noqa: BLE001 - report any engine/stage failure as fatal
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL

    summary = progress(store, project_id, include_page_furniture=args.include_page_furniture)
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


def _project_timing_summary(
    store: SQLiteStore, project_id: int
) -> tuple[float | None, str | None, dict[str, float | None]]:
    """(observed seconds, bottleneck stage, per-engine seconds) for one project.

    Read-only aggregation over persisted latest-attempt job timestamps;
    unmeasured values stay ``None`` instead of being reported as zero.
    """

    from scribeforge.processing_report import build_processing_report

    report = build_processing_report(store, project_id)
    engine_seconds = {item.engine: item.observed_seconds for item in report.engine_timings}
    return report.observed_seconds, report.bottleneck_stage, engine_seconds


def _diagnose_book(args: argparse.Namespace, out: TextIO) -> int:
    import zipfile
    from xml.etree import ElementTree

    from scribeforge.book_diagnosis import diagnose_book

    try:
        result = diagnose_book(StoreLayout(args.store).db_path, args.source, args.epub)
    except (
        OSError,
        ValueError,
        KeyError,
        sqlite3.Error,
        zipfile.BadZipFile,
        ElementTree.ParseError,
    ) as exc:
        print(f"error: could not diagnose book: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(json.dumps(result, ensure_ascii=False, indent=2), file=out)
    return EXIT_OK


def _layout_preview(args: argparse.Namespace, out: TextIO) -> int:
    from scribeforge.pipeline.layout_preview import create_layout_preview

    try:
        preview = create_layout_preview(
            StoreLayout(args.store).db_path,
            project_id=args.project,
            page_index=args.page,
            run_id=args.run,
            block_index=args.block,
            output=args.out,
        )
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"error: could not create layout preview: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(json.dumps(preview, ensure_ascii=False, indent=2), file=out)
    return EXIT_OK


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
            dual_ocr_pages = successful_pages.get("primary_ocr", set()) & successful_pages.get(
                "secondary_ocr", set()
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
            observed, bottleneck, engine_seconds = _project_timing_summary(store, project.id)
            summaries.append(
                {
                    "id": project.id,
                    "title": project.title,
                    "source_uri": project.source_uri,
                    "source_sha256": project.source_sha256,
                    "pages_total": len(pages),
                    "import_state": next(
                        (job.state.value for job in jobs if job.stage is JobStage.IMPORT), None
                    ),
                    "pages_total_meaning": "imported_pages_not_source_page_count",
                    "pages_with_both_ocr_success": len(dual_ocr_pages),
                    "review_queued": len(review_queue(store, project.id)),
                    "job_counts": state_counts,
                    "active_jobs": [
                        {
                            "stage": job.stage.value,
                            "page_index": job.page_index,
                            "attempts": job.attempts,
                        }
                        for job in jobs
                        if job.state.value == "running"
                    ],
                    "failed_jobs": failed_jobs,
                    "observed_seconds": observed,
                    "bottleneck_stage": bottleneck,
                    "engine_seconds": engine_seconds,
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
        observed, bottleneck, engine_seconds = _project_timing_summary(store, project.id)
        observed_text = "unknown" if observed is None else f"{observed:.3f} s"
        print(f"project {project.id}: {project.title} ({project.source_uri})", file=out)
        print(f"  pages: {len(pages)}", file=out)
        print(f"  review queued: {len(review_queue(store, project.id))}", file=out)
        print(f"  observed job time: {observed_text}", file=out)
        print(f"  bottleneck stage: {bottleneck if bottleneck else 'not measurable'}", file=out)
        for engine in sorted(engine_seconds):
            seconds = engine_seconds[engine]
            engine_text = "unknown" if seconds is None else f"{seconds:.3f} s"
            print(f"  engine {engine}: {engine_text}", file=out)
        for job in store.list_jobs(project.id):
            scope = "project" if job.page_index is None else f"page {job.page_index}"
            print(
                f"  {job.stage.value} [{scope}] {job.state.value} attempts={job.attempts}",
                file=out,
            )
    return EXIT_OK


def _events_report(args: argparse.Namespace, out: TextIO) -> int:
    from scribeforge.pipeline.event_analysis import (
        analyze_events,
        events_json,
        parse_events,
        render_events_markdown,
    )

    if not args.file.is_file():
        print(f"error: event log not found: {args.file}", file=sys.stderr)
        return EXIT_FATAL
    if args.top < 1:
        print("error: --top must be >= 1", file=sys.stderr)
        return EXIT_FATAL
    with args.file.open("r", encoding="utf-8") as handle:
        analysis = analyze_events(parse_events(handle))
    if args.json:
        print(
            events_json(
                analysis,
                top=args.top,
                estimate_remaining_pages=getattr(args, "estimate_remaining", None),
            ),
            file=out,
            end="",
        )
    else:
        print(render_events_markdown(analysis, top=args.top), file=out, end="")
    if getattr(args, "estimate_remaining", None) is not None:
        from scribeforge.domain.scheduling import estimate_remaining_seconds, plan_book_schedule

        if args.estimate_remaining < 0:
            print("error: --estimate-remaining must be >= 0", file=sys.stderr)
            return EXIT_FATAL
        if not args.json:
            # In --json mode the estimate is part of the JSON payload above.
            observed = [page.elapsed_seconds for page in analysis.pages]
            workers = analysis.book_workers or 1
            estimated = estimate_remaining_seconds(
                observed, remaining_pages=args.estimate_remaining, workers=workers
            )
            print(
                f"estimated remaining: {estimated:.1f} s for {args.estimate_remaining} page(s) "
                f"at {workers} worker(s) (observed mean page time over {len(observed)} page(s))",
                file=out,
            )
            if getattr(args, "plan", False) and analysis.book_pages:
                from scribeforge.domain.scheduling import render_schedule_markdown

                plan = plan_book_schedule(
                    observed, total_pages=analysis.book_pages, workers=workers
                )
                print(render_schedule_markdown(plan, workers=workers), file=out, end="")
    return 0


def _processing_report(args: argparse.Namespace, out: TextIO) -> int:
    store = _open_store(args.store)
    if store is None:
        print(f"error: no store found at {StoreLayout(args.store).db_path}", file=sys.stderr)
        return EXIT_FATAL
    projects = store.list_projects()
    if args.project is None:
        if not projects:
            print("error: no projects in store", file=sys.stderr)
            return EXIT_FATAL
        if len(projects) != 1:
            print("error: multiple projects; select one with --project", file=sys.stderr)
            return EXIT_FATAL
        project_id = projects[0].id
    else:
        project_id = args.project
        if not any(project.id == project_id for project in projects):
            print(f"error: unknown project ID: {project_id}", file=sys.stderr)
            return EXIT_FATAL
    if getattr(args, "top", 10) < 1:
        print("error: --top must be >= 1", file=sys.stderr)
        return EXIT_FATAL
    try:
        report = build_processing_report(store, project_id)
    except (KeyError, ValueError) as exc:
        print(f"error: cannot build processing report: {exc}", file=sys.stderr)
        return EXIT_FATAL
    event_summary = None
    if getattr(args, "events", None) is not None:
        from scribeforge.pipeline.events import summarize_events

        event_summary = summarize_events(Path(args.events).read_text(encoding="utf-8").splitlines())
    if getattr(args, "compare", None) is not None:
        import json as _json

        from scribeforge.processing_report import (
            compare_report_dicts,
            processing_report_dict,
            render_comparison_markdown,
        )

        compare_path: Path = args.compare
        if not compare_path.is_file():
            print(f"error: comparison report not found: {compare_path}", file=sys.stderr)
            return EXIT_FATAL
        try:
            old_payload = _json.loads(compare_path.read_text(encoding="utf-8"))
        except _json.JSONDecodeError as exc:
            print(f"error: comparison report is not valid JSON: {exc}", file=sys.stderr)
            return EXIT_FATAL
        if not isinstance(old_payload, dict):
            print("error: comparison report must be a JSON object", file=sys.stderr)
            return EXIT_FATAL
        try:
            comparison = compare_report_dicts(old_payload, processing_report_dict(report))
        except ValueError as exc:
            print(f"error: cannot compare reports: {exc}", file=sys.stderr)
            return EXIT_FATAL
        out.write(render_comparison_markdown(comparison))
        return EXIT_OK
    if args.json:
        out.write(
            processing_report_json(report, event_summary=event_summary, slowest_limit=args.top)
        )
    else:
        text = render_processing_report(report, slowest_limit=args.top)
        if event_summary is not None:
            from scribeforge.processing_report import render_event_summary

            text = text + "\n" + render_event_summary(event_summary)
        out.write(text)
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


def _rehash_pages(args: argparse.Namespace, out: TextIO) -> int:
    """Record page asset digests for pages imported before schema v11."""

    store = SQLiteStore(StoreLayout(args.store).db_path)
    try:
        store.initialize()
    except (OSError, sqlite3.Error) as exc:
        print(f"error: could not open store at {args.store}: {exc}", file=sys.stderr)
        return EXIT_FATAL
    try:
        rehashed, missing = store.rehash_missing_page_assets()
    except (OSError, sqlite3.Error, PageAssetError) as exc:
        print(f"error: could not rehash page evidence: {exc}", file=sys.stderr)
        return EXIT_FATAL
    payload = {
        "store": str(args.store),
        "rehashed": rehashed,
        "missing": missing,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=out)
    else:
        print(
            f"rehash-pages: {rehashed} page digest(es) recorded, "
            f"{missing} page raster(s) missing from disk",
            file=out,
        )
    return EXIT_FATAL if missing else EXIT_OK


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
    layout = StoreLayout(args.store)
    if args.json:
        project_reports: list[dict[str, object]] = []
        for project in projects:
            pages = {page.page_index: page for page in store.list_pages(project.id)}
            item_reports: list[dict[str, object]] = []
            for queued in review_queue(
                store,
                project.id,
                include_page_furniture=args.include_page_furniture,
            ):
                detail = review_detail(
                    store,
                    project.id,
                    queued.page_index,
                    queued.pair_index,
                    crops_dir=layout.crops_dir,
                    furniture_reason=queued.furniture_reason,
                )
                page = pages[detail.page_index]
                frame_kind = "unknown"
                frame_path = None
                trusted_crop_path = None
                try:
                    verification_id = detail.verification_id
                    if verification_id is None:
                        raise ValueError("review has no verification identifier")
                    frame, path = verification_frame(store, project.id, page, verification_id)
                    frame_kind = frame.kind
                    frame_path = str(path)
                    pair_reviews = [
                        review
                        for review in store.list_vlm_reviews(verification_id)
                        if review.id == detail.vlm_review_id
                    ]
                    if pair_reviews:
                        evidence = verified_vlm_crop(
                            store, verification_id, frame, pair_reviews[-1]
                        )
                        trusted_crop_path = evidence.crop_path
                except (ValueError, OSError, KeyError):
                    # Legacy or corrupt evidence must not be represented as a known frame.
                    pass
                item_reports.append(
                    {
                        "page_index": detail.page_index,
                        "pair_index": detail.pair_index,
                        "review_token": detail.review_token,
                        "score": detail.score,
                        "reasons": [reason.value for reason in detail.reasons],
                        "furniture_reason": detail.furniture_reason,
                        "primary_text": detail.primary_text,
                        "secondary_text": detail.secondary_text,
                        "primary_line_indexes": list(detail.primary_line_indexes),
                        "secondary_line_indexes": list(detail.secondary_line_indexes),
                        "alignment_status": detail.alignment_status.value,
                        "ambiguity": (
                            None
                            if detail.ambiguity is None
                            else {
                                "reason": detail.ambiguity.reason.value,
                                "score_margin": detail.ambiguity.score_margin,
                                "alternatives": [
                                    {
                                        "primary_line_indexes": list(
                                            alternative.primary_line_indexes
                                        ),
                                        "secondary_line_indexes": list(
                                            alternative.secondary_line_indexes
                                        ),
                                        "score": alternative.score,
                                        "text_similarity": alternative.text_similarity,
                                        "cross_axis_distance": (alternative.cross_axis_distance),
                                    }
                                    for alternative in detail.ambiguity.alternatives
                                ],
                            }
                        ),
                        "vlm_text": detail.vlm_text,
                        "vlm_uncertain": detail.vlm_uncertain,
                        "crop_path": trusted_crop_path,
                        "source_image_path": page.source_image,
                        "coordinate_frame": frame_kind,
                        "ocr_frame_image_path": frame_path,
                        "source_box": _bounding_box_json(detail.source_box),
                        "primary_box": _bounding_box_json(detail.primary_box),
                        "secondary_box": _bounding_box_json(detail.secondary_box),
                        "conflicts": [
                            {
                                "kind": conflict.kind.value,
                                "primary_text": conflict.primary_text,
                                "secondary_text": conflict.secondary_text,
                                "primary_start": conflict.primary_start,
                                "primary_end": conflict.primary_end,
                                "secondary_start": conflict.secondary_start,
                                "secondary_end": conflict.secondary_end,
                            }
                            for conflict in detail.conflicts
                        ],
                    }
                )
            project_reports.append(
                {
                    "id": project.id,
                    "title": project.title,
                    "source_uri": project.source_uri,
                    "items": item_reports,
                }
            )
        print(
            json.dumps(
                {"schema_version": 1, "store": str(args.store), "projects": project_reports},
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
        items = review_queue(
            store,
            project.id,
            include_page_furniture=args.include_page_furniture,
        )
        print(f"project {project.id}: {project.title}", file=out)
        print(f"review queued: {len(items)}", file=out)
        pages = {page.page_index: page for page in store.list_pages(project.id)}
        for item in items:
            detail = review_detail(
                store,
                project.id,
                item.page_index,
                item.pair_index,
                crops_dir=layout.crops_dir,
                furniture_reason=item.furniture_reason,
            )
            reasons = ",".join(reason.value for reason in detail.reasons)
            print(
                f"  page {detail.page_index} pair {detail.pair_index} "
                f"score={detail.score:.2f} reasons={reasons}",
                file=out,
            )
            if detail.furniture_reason is not None:
                print(f"    page_furniture={detail.furniture_reason}", file=out)
            print(
                f"    primary_lines={list(detail.primary_line_indexes)} "
                f"secondary_lines={list(detail.secondary_line_indexes)} "
                f"status={detail.alignment_status.value}",
                file=out,
            )
            print(f"    primary={_render_text(detail.primary_text)}", file=out)
            print(f"    secondary={_render_text(detail.secondary_text)}", file=out)
            if detail.ambiguity is not None:
                print(
                    f"    ambiguity={detail.ambiguity.reason.value} "
                    f"margin={detail.ambiguity.score_margin:.4f} "
                    f"alternatives={len(detail.ambiguity.alternatives)}",
                    file=out,
                )
            if detail.vlm_text is None:
                print("    vlm=none", file=out)
            else:
                uncertain = "true" if detail.vlm_uncertain else "false"
                print(f'    vlm="{detail.vlm_text}" uncertain={uncertain}', file=out)
            trusted_crop_path = None
            text_frame_kind = "unknown"
            text_frame_image: str | None = None
            try:
                verification_id = store.latest_verification_id(project.id, item.page_index)
                frame, path = verification_frame(
                    store, project.id, pages[item.page_index], verification_id
                )
                text_frame_kind = frame.kind
                text_frame_image = str(path)
                reviews = [
                    review
                    for review in store.list_vlm_reviews(verification_id)
                    if review.pair_index == item.pair_index
                ]
                if reviews:
                    trusted_crop_path = verified_vlm_crop(
                        store, verification_id, frame, reviews[-1]
                    ).crop_path
            except (ValueError, OSError, KeyError):
                # Legacy or corrupt evidence must not be represented as a known frame.
                pass
            # Boxes and crops are coordinates in the OCR frame, which may be the
            # preprocessed raster rather than the original scan (#124): say so
            # explicitly instead of letting a reviewer misread the frame.
            print(
                f"    frame={text_frame_kind} frame_image={text_frame_image or 'unknown'}", file=out
            )
            print(f"    crop={trusted_crop_path or 'none'}", file=out)
    return EXIT_OK


def _bounding_box_json(box: BoundingBox | None) -> dict[str, float] | None:
    if box is None:
        return None
    return {"x": box.x, "y": box.y, "width": box.width, "height": box.height}


def _evidence_search(args: argparse.Namespace, out: TextIO) -> int:
    try:
        query = EvidenceSearchQuery(
            project_id=args.project_id,
            source=args.source,
            term=args.term,
            page_start=args.page_start,
            page_end=args.page_end,
            actor=args.actor,
            snapshot_id=args.snapshot_id,
            before_id=args.before_id,
            record_id=args.record_id,
            limit=args.limit,
        )
        result = read_evidence_search(StoreLayout(args.store).db_path, query)
        payload = json.dumps(result, ensure_ascii=True, allow_nan=False)
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"error: could not search evidence: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(payload, file=out)
    return EXIT_OK


def _ocr_evidence(args: argparse.Namespace, out: TextIO) -> int:
    try:
        query = EvidenceQuery(
            args.project_id, args.section, args.page, args.run, args.after, args.limit
        )
        result = read_ocr_evidence(StoreLayout(args.store).db_path, query)
        payload = json.dumps(result, ensure_ascii=True, allow_nan=False)
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"error: could not read OCR evidence: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(payload, file=out)
    return EXIT_OK


def _review_history(args: argparse.Namespace, out: TextIO) -> int:
    try:
        query = HistoryQuery(
            project_id=args.project_id,
            page=args.page,
            pair=args.pair,
            actor=args.actor,
            snapshot_id=args.snapshot_id,
            before_id=args.before_id,
            decision_id=args.decision_id,
            limit=args.limit,
        )
        payload = read_review_history(StoreLayout(args.store).db_path, query)
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"error: could not read decision history: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True), file=out)
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

    project = (
        projects[0]
        if args.project_id is None
        else next((candidate for candidate in projects if candidate.id == args.project_id), None)
    )
    if project is None:
        print(f"error: no project {args.project_id} in store", file=sys.stderr)
        return EXIT_FATAL

    try:
        decision_id = record_human_decision(
            store, project.id, args.page, args.pair, args.text, review_token=args.review_token
        )
    except (ValueError, DecisionAlreadyExists) as exc:
        print(f"error: review decision was not recorded: {exc}", file=sys.stderr)
        return EXIT_PARTIAL
    except KeyError as exc:
        message = exc.args[0] if exc.args else exc
        print(f"error: {message}", file=sys.stderr)
        return EXIT_FATAL

    print(
        f"recorded human decision #{decision_id} for project {project.id} "
        f"page {args.page} pair {args.pair}",
        file=out,
    )
    return EXIT_OK


def _textless_preview(args: argparse.Namespace, out: TextIO) -> int:
    try:
        store = SQLiteStore(StoreLayout(args.store).db_path, read_only=True)
        result = textless_preview(store, args.project_id, args.page)
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        print(f"error: could not prepare textless preview: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(json.dumps(result, ensure_ascii=True, allow_nan=False), file=out)
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

    if args.confirmation_token is not None:
        if args.project_id is None:
            print("error: --project-id is required with --confirmation-token", file=sys.stderr)
            return EXIT_FATAL
        try:
            confirmation_id = submit_textless_confirmation(
                store,
                args.project_id,
                args.page,
                token=args.confirmation_token,
                kind=args.kind,
                reason=args.reason,
            )
        except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
            print(f"error: confirmation not recorded: {exc}", file=sys.stderr)
            return EXIT_FATAL
        if args.json:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": confirmation_id,
                        "project_id": args.project_id,
                        "page_index": args.page,
                        "kind": args.kind,
                    }
                ),
                file=out,
            )
        else:
            print(
                f"confirmed page {args.page} as {args.kind} (confirmation #{confirmation_id})",
                file=out,
            )
        return EXIT_OK
    if args.project_id is not None or args.json:
        print("error: --project-id and --json require --confirmation-token", file=sys.stderr)
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
        f"confirmed page {args.page} as {args.kind} (confirmation #{confirmation_id}): {reason}",
        file=out,
    )
    return EXIT_OK


def _compare_processing_reports(args: argparse.Namespace, out: TextIO) -> int:
    snapshots = []
    for label in ("baseline", "candidate"):
        try:
            snapshots.append(report_comparison.load_snapshot(getattr(args, label)))
        except (ValueError, OSError) as exc:
            print(f"error: {label} report: {exc}", file=sys.stderr)
            return EXIT_FATAL
    try:
        result = report_comparison.compare_snapshots(
            snapshots[0],
            snapshots[1],
            options=report_comparison.CompareOptions(
                args.filter,
                args.order,
                args.offset,
                args.limit,
                args.baseline_sha256,
                args.candidate_sha256,
                args.allow_unmatched,
            ),
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(json.dumps(result, ensure_ascii=True, allow_nan=False), file=out)
    return EXIT_OK


def _verify_export(args: argparse.Namespace, out: TextIO) -> int:
    try:
        result = verify_export_artifact(args.file, expected_source=args.source_sha256)
    except (ValueError, OSError) as exc:
        print(f"error: could not verify export: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(json.dumps(result, ensure_ascii=True, allow_nan=False), file=out)
    return EXIT_OK


def _export_readiness(args: argparse.Namespace, out: TextIO) -> int:
    try:
        result = read_export_readiness(
            StoreLayout(args.store).db_path,
            ReadinessOptions(
                include_page_furniture=args.include_page_furniture,
                strict_acceptance=args.strict_acceptance,
                page=args.page,
                limit=args.limit,
            ),
        )
        payload = json.dumps(result, ensure_ascii=True, allow_nan=False)
    except (ValueError, sqlite3.Error, OSError) as exc:
        print(f"error: could not check export readiness: {exc}", file=sys.stderr)
        return EXIT_FATAL
    print(payload, file=out)
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
            "    if it is a plate or illustration with no caption to read: --kind non_text",
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
            else datetime.now(timezone.utc)
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL
    author = args.author if args.author and args.author.strip() else None

    project = projects[0]
    try:
        book = assemble_verified_content(
            store,
            project.id,
            include_page_furniture=args.include_page_furniture,
        )
    except UnresolvedContentError as exc:
        _report_blocked_export(exc, sys.stderr)
        return EXIT_FATAL
    except PageImageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL
    except ValueError as exc:
        # Fail closed on ambiguous historical batches (#150): never export a
        # decision that cannot be bound to the page's latest verification run.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL

    from scribeforge.pipeline.layout_evidence import collect_layout_observations

    try:
        layout_observations = collect_layout_observations(
            store,
            project.id,
            embedded_pages=(
                frozenset(image.page_index for image in book.images)
                if args.out.suffix.lower() == ".epub"
                else frozenset()
            ),
        )
    except (ValueError, KeyError, sqlite3.Error) as exc:
        print(f"error: could not inspect OCR layout evidence: {exc}", file=sys.stderr)
        return EXIT_FATAL

    if not book.pages:
        print("error: no pages to export", file=sys.stderr)
        return EXIT_FATAL
    summary = book.acceptance
    if strict_acceptance_blocks(book, args.strict_acceptance):
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

    normalization_policy = NormalizationPolicy(args.normalization)
    if normalization_policy is NormalizationPolicy.SIMPLIFIED:
        converter, converter_name = opencc_simplifier()
        derived = derive_normalized_book(
            book,
            normalization_policy,
            converter=converter,
            converter_name=converter_name,
        )
    else:
        derived = derive_normalized_book(book, normalization_policy)
    book = derived.book
    normalization_path = Path(f"{args.out}.normalization.json")

    try:
        if suffix == ".epub":
            data = build_epub(
                book,
                identifier=f"urn:scribeforge:{project.source_sha256}",
                language=args.language,
                modified=modified,
                author=author,
                layout=args.layout,
                normalization=normalization_policy.value,
            )
        else:
            data = build_docx(
                book,
                identifier=f"urn:scribeforge:{project.source_sha256}",
                language=args.language,
                modified=modified,
                author=author,
                normalization=normalization_policy.value,
            )
        output = FileFingerprint.from_bytes(data)
        write_atomic(args.out, data)
        del data
        output_sha256 = output.sha256
        normalization_bytes = normalization_report_json(
            derived.report, output_file=args.out.name, output_sha256=output_sha256
        ).encode("utf-8")
        write_atomic(normalization_path, normalization_bytes)
        receipt = receipt_path(args.out)
        write_atomic(
            receipt,
            make_export_receipt(
                args.out,
                artifact=output,
                normalization=FileFingerprint.from_bytes(normalization_bytes),
                source_sha256=project.source_sha256,
                project_id=project.id,
                title=book.title,
                timestamp=modified.isoformat(),
                strict_acceptance=args.strict_acceptance,
                include_page_furniture=args.include_page_furniture,
                policy=normalization_policy.value,
                pages=len(book.pages),
                accepted_pairs=summary.pairs,
            ),
        )
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL

    # DOCX is written page by page, so a layout would be a claim about a file that
    # was never re-flowed; only EPUB reports one.
    written = build_flow(book, layout=args.layout) if suffix == ".epub" else None
    if args.json:
        report = {
            "output": str(args.out),
            "receipt": str(receipt),
            "pages": len(book.pages),
            "chapters": len(book.chapters),
            "images": len(book.images) if suffix == ".epub" else 0,
            "cover_image": (
                None
                if suffix != ".epub" or book.cover_image is None
                else {
                    "page": book.cover_image.page_index,
                    "file": book.cover_image.file_name,
                    "media_type": book.cover_image.media_type,
                    "sha256": book.cover_image.sha256,
                }
            ),
            "confirmed_blank_pages": list(book.confirmed_blank_pages),
            "empty_pairs": len(book.empty_pairs),
            "layout_observations": [entry.as_json() for entry in layout_observations],
            "layout_notice": (
                "OCR layout observations are not human confirmation; unreported regions "
                "may still exist. Mixed-page figures and tables are not embedded."
            ),
            "docx_omitted_page_images": (
                [image.page_index for image in book.images] if suffix == ".docx" else []
            ),
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
            "dropped_furniture_details": [
                {
                    "page_index": item.page_index,
                    "pair_index": item.pair_index,
                    "verification_id": item.verification_id,
                    "primary_line_indexes": list(item.primary_line_indexes),
                    "secondary_line_indexes": list(item.secondary_line_indexes),
                    "text": item.text,
                    "reason": item.reason,
                }
                for item in book.dropped_furniture
            ],
            "normalization": {
                "policy": derived.report.policy.value,
                "converter": derived.report.converter,
                "source_sha256": derived.report.source_sha256,
                "normalized_sha256": derived.report.normalized_sha256,
                "change_count": len(derived.report.changes),
                "output_sha256": output_sha256,
                "report": str(normalization_path),
            },
        }
        if written is not None:
            report["layout"] = written.layout
            report["documents"] = len(written.documents)
            report["paragraphs"] = len(written.paragraphs)
        print(json.dumps(report, ensure_ascii=False, indent=2), file=out)
        return EXIT_OK

    print(f"exported {len(book.pages)} page(s) to {args.out}", file=out)
    for observation in layout_observations:
        if observation.may_be_omitted:
            print(f"warning: page {observation.page_index}: {observation.warning()}", file=out)
    print(
        "notice: OCR layout reports may miss figures or tables; inspect source pages. "
        "Mixed-page figures and tables are not embedded.",
        file=out,
    )
    print(f"export receipt: {receipt}", file=out)
    print(
        f"normalization: {derived.report.policy.value} "
        f"({len(derived.report.changes)} change(s)); "
        f"report: {normalization_path}",
        file=out,
    )
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
    if book.images:
        if suffix == ".epub":
            covers = sum(1 for image in book.images if image.is_cover)
            print(
                f"page images: {len(book.images)} embedded "
                f"(cover {covers}, plate {len(book.images) - covers})",
                file=out,
            )
        else:
            print(
                "warning: DOCX export writes text only, so "
                f"{len(book.images)} page image(s) are not in this file",
                file=sys.stderr,
            )
    if book.chapters:
        print(
            f"table of contents: {len(book.chapters)} chapter(s) from the book's structure",
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
        selected_project = next((item for item in projects if item.id == args.project_id), None)
        if selected_project is None:
            print(f"error: unknown project ID: {args.project_id}", file=sys.stderr)
            return EXIT_FATAL

    project = selected_project
    from scribeforge.pipeline.import_integrity import require_completed_import

    try:
        require_completed_import(store, project.id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL
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

    modified = datetime.now(timezone.utc)
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
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _verify_corpus_fingerprint(
    manifest_path: Path,
    manifest: BenchmarkManifest,
    root: Path,
    expected: CorpusFingerprint,
) -> bool:
    """Refuse a result if evidence changed after it was measured."""

    try:
        current = fingerprint_corpus(manifest_path, manifest, root)
    except (OSError, ValueError) as exc:
        print(f"error: cannot recheck benchmark corpus: {exc}", file=sys.stderr)
        return False
    if current != expected:
        print(
            "error: benchmark corpus changed while the benchmark was running; "
            "refusing to write a report",
            file=sys.stderr,
        )
        return False
    return True


def _benchmark(args: argparse.Namespace, out: TextIO, factory: BenchmarkEngineFactory) -> int:
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
    try:
        corpus_fingerprint = fingerprint_corpus(manifest_path, manifest, root)
    except (OSError, ValueError) as exc:
        print(f"error: cannot fingerprint benchmark corpus: {exc}", file=sys.stderr)
        return EXIT_FATAL

    if args.oracle_ceiling:
        return _oracle_ceiling(args, out, manifest, root, manifest_path, corpus_fingerprint)

    backends: list[str] = list(args.engine) if args.engine else ["mineru", "paddleocr"]
    if args.include_pipeline and (len(backends) != 2 or set(backends) != set(_PIPELINE_BACKENDS)):
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
            missing = [name for name in ("mineru", "paddleocr") if name not in engines_by_name]
            if missing:
                raise ValueError(
                    "benchmark factory did not provide required backend(s): " + ", ".join(missing)
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

    if not _verify_corpus_fingerprint(manifest_path, manifest, root, corpus_fingerprint):
        return EXIT_FATAL

    report_text = render_report(
        reports,
        corpus=str(manifest_path),
        manifest_sha256=corpus_fingerprint.manifest_sha256,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        corpus_sha256=corpus_fingerprint.corpus_sha256,
        ground_truth_review_status=manifest.ground_truth_review_status,
        ground_truth_note=manifest.ground_truth_note,
        manifest_license=manifest.license,
    )
    try:
        args.out.write_text(report_text, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write report: {exc}", file=sys.stderr)
        return EXIT_FATAL
    want_json = (
        getattr(args, "report_json", None) is not None or getattr(args, "history", None) is not None
    )
    if want_json:
        json_text = render_report_json(
            reports,
            corpus=str(manifest_path),
            manifest_sha256=corpus_fingerprint.manifest_sha256,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            corpus_sha256=corpus_fingerprint.corpus_sha256,
            ground_truth_review_status=manifest.ground_truth_review_status,
            ground_truth_note=manifest.ground_truth_note,
            manifest_license=manifest.license,
        )
        if getattr(args, "report_json", None) is not None:
            try:
                args.report_json.write_text(json_text, encoding="utf-8")
            except OSError as exc:
                print(f"error: cannot write JSON report: {exc}", file=sys.stderr)
                return EXIT_FATAL
        if getattr(args, "history", None) is not None:
            from scribeforge.benchmark.trend import append_history

            try:
                append_history(args.history, json.loads(json_text))
            except (OSError, ValueError) as exc:
                print(f"error: cannot append to history: {exc}", file=sys.stderr)
                return EXIT_FATAL

    print(
        f"wrote benchmark report for {len(reports)} variant(s) to {args.out}",
        file=out,
    )
    return EXIT_OK


def _benchmark_compare(args: argparse.Namespace, out: TextIO) -> int:
    """Compare two benchmark report JSONs; exit 3 on gated regression (#62/#68/#90)."""

    from scribeforge.benchmark.regression import (
        RegressionError,
        RegressionThresholds,
        compare_payloads,
        load_report_json,
        regression_json,
        render_regression_markdown,
    )

    try:
        thresholds = RegressionThresholds(
            cer_pp=args.cer_pp, wer_pp=args.wer_pp, reading_order_pp=args.ro_pp
        )
        baseline = load_report_json(args.baseline)
        current = load_report_json(args.current)
    except (RegressionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL

    report = compare_payloads(baseline, current)
    text = (
        regression_json(report, thresholds)
        if args.json
        else render_regression_markdown(report, thresholds)
    )
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    else:
        out.write(text)
    failures = report.gate_failures(thresholds)
    print(
        f"benchmark-compare: gate {'FAIL' if failures else 'PASS'} "
        f"({len(failures)} regression(s) beyond threshold)",
        file=out,
    )
    if args.fail_on_regression and failures:
        return 3
    return EXIT_OK


def _benchmark_trend(args: argparse.Namespace, out: TextIO) -> int:
    """Render metric trends over a benchmark history; exit 3 on degrading trend."""

    from scribeforge.benchmark.trend import (
        DEFAULT_METRICS,
        HistoryError,
        analyze_series,
        extract_series,
        load_history,
        render_trend_markdown,
        trend_json,
    )

    try:
        history = load_history(args.history)
    except HistoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FATAL
    if args.window < 1:
        print("error: --window must be >= 1", file=sys.stderr)
        return EXIT_FATAL
    if args.max_adverse_pp < 0:
        print("error: --max-adverse-pp must be non-negative", file=sys.stderr)
        return EXIT_FATAL

    engines = args.engine or sorted({v.get("engine") for p in history for v in p["variants"]})
    metrics = args.metric or list(DEFAULT_METRICS)
    analyses = [
        analyze_series(
            extract_series(history, engine=engine, scope=args.scope, metric=metric),
            window=args.window,
            max_adverse_pp=args.max_adverse_pp,
        )
        for engine in engines
        for metric in metrics
    ]
    text = (
        trend_json(
            analyses,
            history=str(args.history),
            run_count=len(history),
            window=args.window,
            max_adverse_pp=args.max_adverse_pp,
        )
        if args.json
        else render_trend_markdown(
            analyses,
            history=str(args.history),
            run_count=len(history),
            window=args.window,
            max_adverse_pp=args.max_adverse_pp,
        )
    )
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    else:
        out.write(text)
    degrading = [a for a in analyses if a.gate_fail]
    print(
        f"benchmark-trend: {len(analyses)} series over {len(history)} run(s); "
        f"{'FAIL' if degrading else 'PASS'} ({len(degrading)} degrading beyond threshold)",
        file=out,
    )
    if args.fail_on_trend and degrading:
        return 3
    return EXIT_OK


def _alignment_eval(args: argparse.Namespace, out: TextIO) -> int:
    """Report #79 alignment accuracy on hand-labelled layout fixtures.

    This is a regression/acceptance artifact over synthetic labelled pages, not
    an OCR accuracy measurement: no model is run and no real scan is read.
    """

    from scribeforge.domain.alignment_eval import (
        evaluate_alignment_samples,
        load_alignment_samples,
        render_alignment_report,
    )

    try:
        samples = load_alignment_samples(args.fixtures)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"error: cannot load alignment fixtures: {exc}", file=sys.stderr)
        return EXIT_FATAL

    evaluation = evaluate_alignment_samples(samples)
    totals = evaluation.totals
    if args.json:
        payload = {
            "sample_count": len(evaluation.samples),
            "samples": [
                {
                    "name": item.name,
                    "true_positive": item.true_positive,
                    "false_positive": item.false_positive,
                    "false_negative": item.false_negative,
                    "precision": item.precision,
                    "recall": item.recall,
                    "f1": item.f1,
                    "reading_order_errors": item.reading_order_errors,
                    "cross_column_errors": item.cross_column_errors,
                    "unresolved_ambiguities": item.unresolved_ambiguities,
                    "one_to_many": item.one_to_many,
                    "many_to_one": item.many_to_one,
                    "review_candidates": item.review_candidates,
                }
                for item in evaluation.samples
            ],
            "totals": {
                "true_positive": totals.true_positive,
                "false_positive": totals.false_positive,
                "false_negative": totals.false_negative,
                "precision": totals.precision,
                "recall": totals.recall,
                "f1": totals.f1,
                "reading_order_errors": totals.reading_order_errors,
                "cross_column_errors": totals.cross_column_errors,
                "unresolved_ambiguities": totals.unresolved_ambiguities,
                "one_to_many": totals.one_to_many,
                "many_to_one": totals.many_to_one,
                "review_candidates": totals.review_candidates,
            },
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=out)
    else:
        report = render_alignment_report(evaluation)
        if args.out is not None:
            try:
                args.out.write_text(report, encoding="utf-8")
            except OSError as exc:
                print(f"error: cannot write report: {exc}", file=sys.stderr)
                return EXIT_FATAL
        else:
            print(report, file=out)
    summary = (
        "alignment evaluation: "
        f"precision={totals.precision:.4f} recall={totals.recall:.4f} f1={totals.f1:.4f} "
        f"order_errors={totals.reading_order_errors} "
        f"cross_column={totals.cross_column_errors} "
        f"ambiguities={totals.unresolved_ambiguities} review_queue={totals.review_candidates}"
    )
    # In --json mode stdout must stay pure machine-readable JSON.
    print(summary, file=sys.stderr if args.json else out)
    return EXIT_OK


def _oracle_ceiling(
    args: argparse.Namespace,
    out: TextIO,
    manifest: BenchmarkManifest,
    root: Path,
    manifest_path: Path,
    corpus_fingerprint: CorpusFingerprint,
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
    reference_layout: dict[str, tuple[BoundingBox, ...]] = {}
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
        # Declare the reference geometry so the report can separate spatial
        # missing/extra lines from strict text-based LCS counts. The boxes are
        # aligned with the same scored text the runner computes.
        reference_layout[case.id] = primary.boxes_for_text(scoring_text(text), direction=direction)

    try:
        reports = [
            run_benchmark(manifest, root, primary, reference_layout=reference_layout),
            run_pipeline_benchmark(manifest, root, primary, secondary, label="oracle-ceiling"),
        ]
    except Exception as exc:  # noqa: BLE001 - a harness failure must fail loudly
        print(f"error: oracle ceiling failed: {exc}", file=sys.stderr)
        return EXIT_FATAL

    if not _verify_corpus_fingerprint(manifest_path, manifest, root, corpus_fingerprint):
        return EXIT_FATAL

    report_text = render_report(
        reports,
        corpus=str(manifest_path),
        manifest_sha256=corpus_fingerprint.manifest_sha256,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        corpus_sha256=corpus_fingerprint.corpus_sha256,
        ground_truth_review_status=manifest.ground_truth_review_status,
        ground_truth_note=manifest.ground_truth_note,
        manifest_license=manifest.license,
    )
    try:
        args.out.write_text(report_text, encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot write report: {exc}", file=sys.stderr)
        return EXIT_FATAL
    if getattr(args, "report_json", None) is not None:
        json_text = render_report_json(
            reports,
            corpus=str(manifest_path),
            manifest_sha256=corpus_fingerprint.manifest_sha256,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            corpus_sha256=corpus_fingerprint.corpus_sha256,
            ground_truth_review_status=manifest.ground_truth_review_status,
            ground_truth_note=manifest.ground_truth_note,
            manifest_license=manifest.license,
        )
        try:
            args.report_json.write_text(json_text, encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write JSON report: {exc}", file=sys.stderr)
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

    status = (
        "uncertain"
        if reading.uncertain
        else ("blank" if not reading.text.strip() else "transcribed")
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
            print(
                "This result is diagnostic only; it has not been accepted as project text.",
                file=out,
            )

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
    from scribeforge.runtime.sizes import estimate_payload, preflight

    backends: dict[str, dict[str, object]] = {}
    optional_backends = getattr(overview, "optional_backends", {}) or {}
    named = [("mineru", overview.mineru, False), ("paddleocr", overview.paddleocr, False)]
    named += [(name, status, True) for name, status in sorted(optional_backends.items())]
    # Issue #214: per-backend size estimates travel with each backend entry so
    # the desktop UI can budget the first run before any download starts.
    estimates = estimate_payload(name for name, _, _ in named)
    for name, backend_status, optional in named:
        environment = layout.environment_dir(name)
        backends[name] = {
            "state": backend_status.state,
            "failed_step": backend_status.failed_step,
            "environment_path": str(environment),
            "environment_exists": environment.is_dir(),
            "optional": optional,
            "estimates": estimates.get(name, {}),
        }
    # Issue #214: disk preflight for the core first-run backends; the UI blocks
    # "初始化环境" when the runtime-root drive cannot fit the estimate.
    from scribeforge.runtime.location import external_model_location

    core_names = [name for name, _, optional in named if not optional]
    model_location = external_model_location()
    if args.location_profile or model_location is not None:
        core_names = [
            name
            for name in core_names
            if backends[name]["state"] != "ready"
            or not layout.python_executable(name, profile.os_name).is_file()
        ]
        for name in core_names:
            if backends[name]["state"] == "ready":
                backends[name]["state"] = "missing"
    disk = cast(
        dict[str, Any], preflight(runtime_root, core_names, reuse_models=model_location is not None)
    )
    models_path = model_location or layout.models_dir

    paths = {
        "models": str(models_path),
        "models_exists": models_path.is_dir(),
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
        "disk": disk,
    }

    if args.json:
        # ASCII-safe: frozen Windows consoles are cp1252 and would crash on Chinese (#214)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True), file=out)
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
        from scribeforge.runtime.sizes import format_bytes

        print(f"Models: {paths['models']}", file=out)
        print(f"Cache: {paths['cache']}", file=out)
        print(
            f"Disk: free {format_bytes(int(disk['free_bytes']))} / "
            f"total {format_bytes(int(disk['total_bytes']))} on {disk['path']}",
            file=out,
        )
        print(
            f"First-run estimate: ~{format_bytes(int(disk['required_bytes']))} "
            f"({'sufficient' if disk['sufficient'] else 'INSUFFICIENT — see advice'})",
            file=out,
        )
        print(f"Advice: {disk['advice']}", file=out)
    return EXIT_OK


def _runtime_clean(args: argparse.Namespace, out: TextIO) -> int:
    """Remove disposable runtime artifacts and report freed bytes (issue #214)."""
    from scribeforge.runtime.cleanup import clean_runtime_artifacts

    runtime_root: Path = (
        args.runtime_root if args.runtime_root is not None else default_runtime_root()
    )
    try:
        report = cast(
            dict[str, Any],
            clean_runtime_artifacts(
                runtime_root,
                include_cache=bool(args.cache),
                include_models=bool(args.models),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - cleanup must stay actionable
        print(f"error: could not clean runtime artifacts: {exc}", file=sys.stderr)
        return EXIT_FATAL
    from scribeforge.runtime.sizes import format_bytes

    if args.json:
        # ASCII-safe: frozen Windows consoles are cp1252 and would crash on Chinese (#214)
        print(json.dumps(report, ensure_ascii=True, sort_keys=True), file=out)
    else:
        print(f"Runtime root: {runtime_root}", file=out)
        print(f"Freed: {format_bytes(int(report['freed_bytes']))}", file=out)
        for removed in report["removed"]:
            print(f"removed: {removed}", file=out)
        print(f"Kept: {', '.join(str(item) for item in report['kept'])}", file=out)
    return EXIT_OK


def _setup(args: argparse.Namespace, out: TextIO) -> int:
    if args.json:
        return _setup_json(args, out)
    if args.backend is not None:
        print("error: --backend requires --json", file=sys.stderr)
        return EXIT_FATAL

    try:
        from scribeforge.ui.runtime_window import run_runtime_setup
    except ImportError as exc:
        print(f"error: the runtime setup wizard needs Tkinter ({exc})", file=sys.stderr)
        return EXIT_FATAL

    runtime_root: Path = (
        args.runtime_root if args.runtime_root is not None else default_runtime_root()
    )
    return run_runtime_setup(runtime_root)


def _setup_json(args: argparse.Namespace, out: TextIO) -> int:
    """Stream step-level setup events and one machine-readable final report."""
    from scribeforge.runtime.manager import RuntimeEvent, RuntimeInstallError
    from scribeforge.runtime.workflow import RuntimeSetupWorkflow

    runtime_root: Path = (
        args.runtime_root if args.runtime_root is not None else default_runtime_root()
    )
    action = "repair" if args.backend is not None else "install"
    workflow = (
        RuntimeSetupWorkflow(runtime_root)
        if args.step_timeout is None
        else RuntimeSetupWorkflow(runtime_root, timeout_seconds=args.step_timeout)
    )
    result_code = EXIT_OK
    message: str | None = None

    def emit_progress(event: RuntimeEvent) -> None:
        record = {
            "schema_version": 1,
            "type": "progress",
            "backend": event.backend,
            "phase": event.phase,
            "state": event.state,
            "step": event.step,
            "completed_steps": event.completed_steps,
            "total_steps": event.total_steps,
            # Issue #214: cumulative estimated byte counters (not measured
            # transfers) so the UI can render download/install progress.
            "downloaded_bytes": event.downloaded_bytes,
            "installed_bytes": event.installed_bytes,
        }
        print(
            json.dumps(record, ensure_ascii=True, sort_keys=True), file=out, flush=True
        )  # ASCII-safe for frozen cp1252 consoles (#214)

    try:
        if args.backend is None:
            workflow.install(on_event=emit_progress)
        else:
            workflow.repair(args.backend, on_event=emit_progress)
    except ProcessingCancelled as exc:
        result_code = EXIT_CANCELLED
        message = str(exc).strip() or "OCR runtime setup was cancelled."
    except RuntimeInstallError as exc:
        result_code = EXIT_PARTIAL
        message = str(exc).strip() or "OCR runtime setup did not complete."
    except Exception as exc:  # noqa: BLE001 - return a useful final JSON record
        result_code = EXIT_FATAL
        message = str(exc).strip() or exc.__class__.__name__

    try:
        overview = workflow.inspect()
    except Exception as exc:  # noqa: BLE001 - preserve machine-readable failure details
        result_code = max(result_code, EXIT_FATAL)
        inspection_error = f"Could not inspect final runtime state: {exc}"
        message = f"{message}; {inspection_error}" if message else inspection_error
        backend_states: dict[str, dict[str, object]] = {
            name: {"state": "unknown", "failed_step": None}
            for name in ("mineru", "paddleocr", "paddleocr-vl", "surya")
        }
    else:
        backend_states = {}
        optional_backends = getattr(overview, "optional_backends", {}) or {}
        statuses = {
            "mineru": overview.mineru,
            "paddleocr": overview.paddleocr,
            **optional_backends,
        }
        for name, status in statuses.items():
            state = getattr(status, "state", "unknown")
            failed_step = getattr(status, "failed_step", None)
            backend_states[name] = {
                "state": state if isinstance(state, str) else "unknown",
                "failed_step": failed_step if isinstance(failed_step, str) else None,
            }
        target_backends = (
            (args.backend,)
            if args.backend is not None
            else (
                "mineru",
                "paddleocr",
            )
        )
        if message is None and any(
            backend_states[name]["state"] != "ready" for name in target_backends
        ):
            result_code = EXIT_PARTIAL
            message = "One or more requested OCR backends are not ready."

    # Issue #214: make the keep/clean policy explicit after success, failure
    # or cancellation, so users know exactly what remains on disk.
    report = {
        "schema_version": 1,
        "type": "result",
        "action": action,
        "backend": args.backend,
        "runtime_root": str(runtime_root),
        "success": message is None,
        "message": message,
        "backends": backend_states,
        "retention": {
            "kept": [
                "models",
                "cache",
                "tools",
                "ready backend environments",
                "runtime-state.json",
            ],
            "cleaned": [
                "staging environments (.name.staging-*)",
                "previous environments (.name.previous-*)",
            ],
            "policy": (
                "取消或失败后：已完成的后端环境、模型与缓存会保留以便断点续装；"
                "未完成的临时环境目录会被清理。重新运行会复用已有产物，不会重复下载。"
            ),
        },
    }
    print(
        json.dumps(report, ensure_ascii=True, sort_keys=True), file=out, flush=True
    )  # ASCII-safe for frozen cp1252 consoles (#214)
    return result_code


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


def default_engine_factory(
    runtime_root: Path,
    *,
    worker_timeout_seconds: float = DEFAULT_WORKER_TIMEOUT_SECONDS,
) -> Engines:
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
        timeout_seconds=worker_timeout_seconds,
    )
    secondary = build_runtime_engine(
        layout,
        "paddleocr",
        _worker_script("paddle_worker.py"),
        os_name,
        name="paddleocr",
        status=status,
        timeout_seconds=worker_timeout_seconds,
    )
    return Engines(primary=primary, secondary=secondary, reviewer=None)


def default_benchmark_engine_factory(
    runtime_root: Path,
    backends: Sequence[str],
    *,
    worker_timeout_seconds: float = DEFAULT_WORKER_TIMEOUT_SECONDS,
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
            timeout_seconds=worker_timeout_seconds,
        )
        for backend in backends
    )


def _worker_script(name: str) -> Path:
    return worker_source_root() / "scribeforge" / "workers" / name


if __name__ == "__main__":
    raise SystemExit(main())
