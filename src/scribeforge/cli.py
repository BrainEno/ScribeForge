from __future__ import annotations

import argparse
import hashlib
import json
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
from scribeforge.adapters.runtime_workers import build_runtime_engine
from scribeforge.adapters.sources import open_page_source
from scribeforge.benchmark.corpus import validate_corpus
from scribeforge.benchmark.manifest import load_manifest
from scribeforge.benchmark.report import render_report
from scribeforge.benchmark.runner import run_benchmark
from scribeforge.domain.candidate import OcrCandidateBook, OcrCandidatePage
from scribeforge.domain.ocr import OCREngine
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.pipeline.adjudication import record_human_decision, review_detail, review_queue
from scribeforge.pipeline.assembly import UnresolvedContentError, assemble_verified_content
from scribeforge.pipeline.importing import import_source
from scribeforge.pipeline.orchestrator import process_book
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

_BENCHMARK_BACKENDS = {
    "mineru": "mineru_worker.py",
    "paddleocr": "paddle_worker.py",
}


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

    setup = subparsers.add_parser(
        "setup", help="Open the first-run wizard for private OCR runtimes and models."
    )
    setup.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Private runtime directory (default: the per-user ScribeForge runtime folder).",
    )

    status = subparsers.add_parser(
        "status", help="Show the persisted processing jobs of a project store."
    )
    status.add_argument("--store", type=Path, required=True, help="Project store directory.")
    status.add_argument(
        "--json", action="store_true", help="Emit a stable JSON summary for desktop clients."
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

    ui = subparsers.add_parser(
        "ui", help="Open the read-only desktop shell for a project store."
    )
    ui.add_argument("--store", type=Path, required=True, help="Project store directory.")

    export = subparsers.add_parser(
        "export", help="Write the verified text of a project to an EPUB 3 file."
    )
    export.add_argument("--store", type=Path, required=True, help="Project store directory.")
    export.add_argument("--out", type=Path, required=True, help="Destination EPUB file.")
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
    if args.command == "status":
        return _status(args, out)
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
    factory = default_engine_factory if engine_factory is None else engine_factory
    return _process(args, out, factory)


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
    decisions = sum(page.decision_count for page in outcome.pages)
    queued = sum(len(page.unresolved_pairs) for page in outcome.pages)
    print(f"recorded {decisions} decision(s), {queued} pair(s) need review", file=out)
    for page_index, error in outcome.failed_pages:
        print(f"  page {page_index}: {error}", file=out)
    return EXIT_PARTIAL if outcome.failed_pages else EXIT_OK


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
        print(
            "error: cannot export unresolved content: "
            f"{len(exc.unresolved_pairs)} unresolved pair(s), "
            f"{len(exc.unverified_pages)} unverified page(s); "
            "resolve them with 'scribeforge review'",
            file=sys.stderr,
        )
        return EXIT_FATAL

    if not book.pages:
        print("error: no pages to export", file=sys.stderr)
        return EXIT_FATAL
    if args.title is not None:
        book = replace(book, title=args.title)

    suffix = args.out.suffix.lower()
    if suffix == ".epub":
        writer = write_epub
    elif suffix == ".docx":
        writer = write_docx
    else:
        print(
            f"error: unsupported export extension {suffix or '(none)'}; use .epub or .docx",
            file=sys.stderr,
        )
        return EXIT_FATAL

    try:
        writer(
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

    print(f"exported {len(book.pages)} page(s) to {args.out}", file=out)
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

    backends: list[str] = list(args.engine) if args.engine else ["mineru", "paddleocr"]
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
        reports = tuple(run_benchmark(manifest, root, engine) for engine in engines)
    except Exception as exc:  # noqa: BLE001 - an engine failure must fail as fatal
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
