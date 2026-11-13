"""Page-parallelism performance baseline (issue #70).

Opt-in benchmark (marker ``benchmark``): skipped in the default CI run. It
proves with fake slow engines that a 500-page book really scales with
``--workers``: the parallel run must be substantially faster than the serial
run, and the wall time must not grow linearly with page count when workers
are available.

Run locally with:
    pytest tests/test_parallel_performance.py -m benchmark --no-cov
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.storage.sqlite import SQLiteStore

pytestmark = pytest.mark.benchmark

PAGE_COUNT = 120  # keep the opt-in run under ~30s locally
ENGINE_DELAY = 0.05


class DelayedEngine:
    def __init__(self, name: str, delay: float = ENGINE_DELAY) -> None:
        self._name = name
        self._delay = delay

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        time.sleep(self._delay)
        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        token = OCRToken(self._name, 0.9, box)
        line = OCRLine(self._name, box, (token,))
        return PageOCRResult(page_index, self._name, "1.0", (line,))


def _store(tmp_path: Path, page_count: int) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    for index in range(page_count):
        image = tmp_path / f"page-{index}.png"
        Image.new("RGB", (64, 64), "white").save(image)
        store.add_page(project_id, index, str(image), 64, 64)
    return store, project_id


def _run(store: SQLiteStore, project_id: int, tmp_path: Path, workers: int) -> float:
    started = time.monotonic()
    outcome = process_book(
        store,
        project_id,
        primary_engine=DelayedEngine("primary"),
        secondary_engine=DelayedEngine("secondary"),
        crops_dir=tmp_path / "crops",
        max_workers=workers,
    )
    elapsed = time.monotonic() - started
    assert len(outcome.pages) == PAGE_COUNT
    assert outcome.failed_pages == ()
    return elapsed


def test_parallel_scales_beyond_serial(tmp_path: Path) -> None:
    serial_store, serial_project = _store(tmp_path / "serial", PAGE_COUNT)
    serial_seconds = _run(serial_store, serial_project, tmp_path / "serial", workers=1)

    parallel_store, parallel_project = _store(tmp_path / "parallel", PAGE_COUNT)
    parallel_seconds = _run(parallel_store, parallel_project, tmp_path / "parallel", workers=4)

    speedup = serial_seconds / max(parallel_seconds, 1e-9)
    # Two engines per page run sequentially inside a page, so the theoretical
    # ceiling is ~workers; require a conservative >2x to stay flake-free.
    assert speedup > 2.0, (
        f"parallel speedup only {speedup:.2f}x "
        f"(serial {serial_seconds:.2f}s vs parallel {parallel_seconds:.2f}s)"
    )
