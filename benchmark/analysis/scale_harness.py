"""Scale and robustness measurements for large scanned books.

This harness answers the question *"does the current pipeline survive a
500-800 page scan?"* without requiring a GPU or an installed OCR backend.

Two independent measurements are produced:

1. **Import scale** — real rasterization of real pages from a real PDF through
   the production ``PdfPageSource`` + ``import_source`` path. This measures wall
   time, disk footprint and per-page asset size. No OCR model is involved.

2. **Pipeline + export scale** — the production orchestrator, decision layer,
   assembly and EPUB/DOCX exporters driven by *deterministic synthetic engines*.
   These engines emit fixed geometry and text; they are explicitly **not OCR**
   and their output carries no transcription evidence. They exist only to
   measure how the surrounding machinery behaves at book scale (SQLite query
   counts, per-page timing, export time, memory).

Nothing here is scored as OCR accuracy. Accuracy requires real backends and is
measured by ``scribeforge benchmark`` instead.
"""

from __future__ import annotations

import argparse
import json
import resource
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from scribeforge.adapters.docx import build_docx
from scribeforge.adapters.epub import build_epub
from scribeforge.adapters.pdf_pages import PdfPageSource
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.pipeline.assembly import assemble_verified_content
from scribeforge.pipeline.importing import import_source
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.storage.sqlite import SQLiteStore

LINES_PER_PAGE = 25
TOKENS_PER_LINE = 30


class SyntheticEngine:
    """A deterministic, non-OCR stand-in used only for scale measurement.

    It fabricates line geometry and text so the orchestration, persistence,
    adjudication and export layers can be exercised at book scale. It performs
    no recognition whatsoever and its output must never be treated as text.
    """

    def __init__(self, name: str, *, text_offset: int = 0) -> None:
        self._name = name
        self._text_offset = text_offset

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        lines: list[OCRLine] = []
        for line_index in range(LINES_PER_PAGE):
            y = 0.08 + line_index * 0.032
            box = BoundingBox(0.08, min(y, 0.95), 0.84, 0.025)
            # Both engines agree on the same text here, which is what the
            # "clean page" path looks like: every pair is auto-accepted.
            text = f"P{page_index:04d}L{line_index:02d}" + "字" * self._text_offset
            tokens = tuple(
                OCRToken(text="字", confidence=0.99, box=box)
                for _ in range(TOKENS_PER_LINE)
            )
            lines.append(OCRLine(text=text, box=box, tokens=tokens))
        return PageOCRResult(
            page_index=page_index,
            engine=self._name,
            engine_version="synthetic-scale-harness-1",
            lines=tuple(lines),
        )


def _peak_rss_mb() -> float:
    """Peak resident set size in MiB (Linux/macOS)."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports KiB, macOS reports bytes.
    if usage > 10_000_000:
        return usage / (1024 * 1024)
    return usage / 1024


def _dir_size_bytes(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


@dataclass(frozen=True, slots=True)
class ImportMeasurement:
    page_count: int
    elapsed_seconds: float
    assets_bytes: int
    peak_rss_mb: float

    def as_dict(self) -> dict:
        return {
            "page_count": self.page_count,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "seconds_per_page": round(self.elapsed_seconds / self.page_count, 3),
            "pages_per_minute": round(60.0 * self.page_count / self.elapsed_seconds, 1),
            "assets_mb": round(self.assets_bytes / (1024 * 1024), 1),
            "kb_per_page": round(self.assets_bytes / 1024 / self.page_count, 0),
            "peak_rss_mb": round(self.peak_rss_mb, 1),
        }


def measure_import(
    pdf_path: Path,
    page_count: int,
    work_dir: Path,
    *,
    dpi: int = 300,
) -> ImportMeasurement:
    """Import real pages through the production PDF → asset path."""

    source = PdfPageSource(pdf_path, dpi=dpi)
    if page_count > source.page_count:
        raise ValueError(
            f"requested {page_count} pages but {pdf_path} has {source.page_count}"
        )

    store = SQLiteStore(work_dir / "project.sqlite3")
    store.initialize()
    project_id = store.create_project(
        pdf_path.stem, str(pdf_path.resolve()), "0" * 64
    )

    class _Limited:
        """Expose only the first N pages of a real PdfPageSource."""

        def __init__(self, inner: PdfPageSource, limit: int) -> None:
            self._inner = inner
            self.page_count = limit

        def render_page(self, page_index: int):
            return self._inner.render_page(page_index)

    started = time.perf_counter()
    import_source(store, project_id, _Limited(source, page_count), work_dir / "assets")
    elapsed = time.perf_counter() - started

    return ImportMeasurement(
        page_count=page_count,
        elapsed_seconds=elapsed,
        assets_bytes=_dir_size_bytes(work_dir / "assets"),
        peak_rss_mb=_peak_rss_mb(),
    )


@dataclass(frozen=True, slots=True)
class PipelineMeasurement:
    page_count: int
    process_seconds: float
    assemble_seconds: float
    epub_seconds: float
    docx_seconds: float
    epub_bytes: int
    docx_bytes: int
    db_bytes: int
    decisions: int
    peak_rss_mb: float

    def as_dict(self) -> dict:
        page_count = self.page_count
        return {
            "page_count": page_count,
            "process_seconds": round(self.process_seconds, 2),
            "process_seconds_per_page": round(self.process_seconds / page_count, 3),
            "assemble_seconds": round(self.assemble_seconds, 2),
            "epub_seconds": round(self.epub_seconds, 2),
            "docx_seconds": round(self.docx_seconds, 2),
            "epub_kb": round(self.epub_bytes / 1024, 0),
            "docx_kb": round(self.docx_bytes / 1024, 0),
            "db_mb": round(self.db_bytes / (1024 * 1024), 1),
            "decisions": self.decisions,
            "peak_rss_mb": round(self.peak_rss_mb, 1),
        }


def measure_pipeline(page_count: int, work_dir: Path) -> PipelineMeasurement:
    """Drive orchestration, assembly and export at book scale."""

    store = SQLiteStore(work_dir / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("scale", str(work_dir / "source.pdf"), "0" * 64)
    for page_index in range(page_count):
        store.add_page(project_id, page_index, str(work_dir / "page.png"), 2481, 3728)

    primary = SyntheticEngine("mineru")
    secondary = SyntheticEngine("paddleocr")

    started = time.perf_counter()
    outcome = process_book(
        store,
        project_id,
        primary_engine=primary,
        secondary_engine=secondary,
        reviewer=None,
        crops_dir=work_dir / "crops",
    )
    process_seconds = time.perf_counter() - started
    if outcome.failed_pages:
        raise RuntimeError(f"pipeline failed: {outcome.failed_pages[:3]}")

    started = time.perf_counter()
    book = assemble_verified_content(store, project_id)
    assemble_seconds = time.perf_counter() - started

    identifier = "urn:scribeforge:scale"
    started = time.perf_counter()
    epub = build_epub(
        book,
        identifier=identifier,
        language="zh",
        modified=datetime(2026, 10, 28, tzinfo=UTC),
    )
    epub_seconds = time.perf_counter() - started

    started = time.perf_counter()
    docx = build_docx(
        book,
        identifier=identifier,
        language="zh",
        modified=datetime(2026, 10, 28, tzinfo=UTC),
    )
    docx_seconds = time.perf_counter() - started

    decisions = sum(
        outcome_page.decision_count for outcome_page in outcome.pages
    )

    return PipelineMeasurement(
        page_count=page_count,
        process_seconds=process_seconds,
        assemble_seconds=assemble_seconds,
        epub_seconds=epub_seconds,
        docx_seconds=docx_seconds,
        epub_bytes=len(epub),
        docx_bytes=len(docx),
        db_bytes=(work_dir / "project.sqlite3").stat().st_size,
        decisions=decisions,
        peak_rss_mb=_peak_rss_mb(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", type=Path, default=Path("benchmark/V..pdf"))
    parser.add_argument("--import-pages", type=int, default=40)
    parser.add_argument("--pipeline-pages", type=int, default=562)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report: dict[str, object] = {
        "pdf": str(args.pdf),
        "generated": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    with TemporaryDirectory(prefix="scribeforge-scale-import-") as tmp:
        import_measurement = measure_import(
            args.pdf, args.import_pages, Path(tmp)
        )
        report["import_40_pages_300dpi"] = import_measurement.as_dict()

    with TemporaryDirectory(prefix="scribeforge-scale-pipeline-") as tmp:
        pipeline_measurement = measure_pipeline(args.pipeline_pages, Path(tmp))
        report["pipeline_synthetic"] = pipeline_measurement.as_dict()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("== Import (real PDF rasterization, production path) ==")
        for key, value in report["import_40_pages_300dpi"].items():  # type: ignore[union-attr]
            print(f"  {key}: {value}")
        print()
        print("== Pipeline + export (synthetic engines, NOT OCR) ==")
        for key, value in report["pipeline_synthetic"].items():  # type: ignore[union-attr]
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
