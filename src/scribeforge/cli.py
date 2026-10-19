from __future__ import annotations

import argparse
import hashlib
import platform
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from scribeforge.adapters.runtime_workers import build_runtime_engine
from scribeforge.adapters.sources import open_page_source
from scribeforge.domain.ocr import OCREngine
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.pipeline.importing import import_source
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.runtime.manager import RuntimeManager
from scribeforge.runtime.models import RuntimeLayout
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
        help="Managed runtime root (default: <store>/runtime).",
    )

    status = subparsers.add_parser(
        "status", help="Show the persisted processing jobs of a project store."
    )
    status.add_argument("--store", type=Path, required=True, help="Project store directory.")

    return parser


def run(
    argv: Sequence[str],
    *,
    engine_factory: EngineFactory | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Execute a CLI invocation; injectable for deterministic tests."""

    args = build_parser().parse_args(list(argv))
    out = sys.stdout if stdout is None else stdout
    if args.command == "status":
        return _status(args, out)
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
    try:
        store = SQLiteStore(layout.db_path)
        store.initialize()
        project_id = _ensure_project(store, book)
        imported = import_source(store, project_id, open_page_source(book), layout.assets_dir)
        runtime_root: Path = (
            args.runtime_root if args.runtime_root is not None else layout.root / "runtime"
        )
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
        print(f"error: {exc}", file=sys.stderr)
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
    for page_index, error in outcome.failed_pages:
        print(f"  page {page_index}: {error}", file=out)
    return EXIT_PARTIAL if outcome.failed_pages else EXIT_OK


def _status(args: argparse.Namespace, out: TextIO) -> int:
    layout = StoreLayout(args.store)
    if not layout.db_path.exists():
        print(f"error: no store found at {layout.db_path}", file=sys.stderr)
        return EXIT_FATAL

    store = SQLiteStore(layout.db_path)
    store.initialize()
    projects = store.list_projects()
    if not projects:
        print("no projects", file=out)
        return EXIT_OK

    for project in projects:
        pages = store.list_pages(project.id)
        print(f"project {project.id}: {project.title} ({project.source_uri})", file=out)
        print(f"  pages: {len(pages)}", file=out)
        for job in store.list_jobs(project.id):
            scope = "project" if job.page_index is None else f"page {job.page_index}"
            print(
                f"  {job.stage.value} [{scope}] {job.state.value} attempts={job.attempts}",
                file=out,
            )
    return EXIT_OK


def _ensure_project(store: SQLiteStore, book: Path) -> int:
    existing = store.list_projects()
    if existing:
        return existing[0].id
    return store.create_project(book.stem, str(book), _file_sha256(book))


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


def _worker_script(name: str) -> Path:
    return Path(__file__).resolve().parent / "workers" / name


if __name__ == "__main__":
    raise SystemExit(main())
