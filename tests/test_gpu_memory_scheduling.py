"""GPU memory-budget scheduling tests (issue #70).

On GPU hosts, naively running `cpu_workers` pages at once can exhaust VRAM
because every page holds both OCR engines resident. `resolve_page_worker_count`
caps the page-level parallelism by a memory budget: each in-flight page costs
the sum of its engines' declared VRAM, and the worker count is the largest
number of pages that fits in the budget, never exceeding the CPU-based count
and never below one.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.scheduling import (
    DEFAULT_PAGE_VRAM_MIB,
    engine_vram_mib,
    resolve_page_worker_count,
)
from scribeforge.pipeline.orchestrator import process_book
from scribeforge.storage.sqlite import SQLiteStore


class SchedulingCase:
    def __init__(self, cpu_workers: int, budget: int | None, per_page: int, expected: int) -> None:
        self.cpu_workers = cpu_workers
        self.budget = budget
        self.per_page = per_page
        self.expected = expected


@pytest.mark.parametrize(
    "case",
    [
        # No budget: the CPU-based count wins unchanged.
        SchedulingCase(1, None, 4096, 1),
        SchedulingCase(8, None, 4096, 8),
        # Budget fits every page: CPU count wins.
        SchedulingCase(4, 65536, 4096, 4),
        # Budget caps below the CPU count: 16 GiB / 6 GiB per page -> 2 pages.
        SchedulingCase(8, 16384, 6144, 2),
        # Budget fits exactly one page: always at least one worker.
        SchedulingCase(8, 6144, 6144, 1),
        # Budget smaller than one page still runs one page (never zero).
        SchedulingCase(8, 1024, 6144, 1),
        # Large budget with a single CPU worker stays serial.
        SchedulingCase(1, 1 << 20, 1 << 20, 1),
    ],
    ids=lambda c: f"cpu={c.cpu_workers},budget={c.budget},per_page={c.per_page}",
)
def test_resolve_page_worker_count(case: SchedulingCase) -> None:
    assert (
        resolve_page_worker_count(
            case.cpu_workers, memory_budget_mib=case.budget, per_page_vram_mib=case.per_page
        )
        == case.expected
    )


@pytest.mark.parametrize("bad_budget", [0, -1, -4096])
def test_resolve_page_worker_count_rejects_nonpositive_budget(bad_budget: int) -> None:
    with pytest.raises(ValueError, match="memory_budget_mib"):
        resolve_page_worker_count(4, memory_budget_mib=bad_budget, per_page_vram_mib=4096)


@pytest.mark.parametrize("bad_page", [0, -1])
def test_resolve_page_worker_count_rejects_nonpositive_page_cost(bad_page: int) -> None:
    with pytest.raises(ValueError, match="per_page_vram_mib"):
        resolve_page_worker_count(4, memory_budget_mib=8192, per_page_vram_mib=bad_page)


def test_default_page_vram_is_positive_and_documented_unit() -> None:
    assert DEFAULT_PAGE_VRAM_MIB > 0


class FakeEngine:
    def __init__(self, name: str, text: str = "鸦", vram_mib: int | None = None) -> None:
        self._name = name
        self._text = text
        if vram_mib is not None:
            self.vram_mib = vram_mib

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        token = OCRToken(self._text, 0.9, box)
        line = OCRLine(self._text, box, (token,))
        return PageOCRResult(page_index, self._name, "1.0", (line,))


class DeclaredVramEngine(FakeEngine):
    def __init__(self, name: str, vram_mib: int) -> None:
        super().__init__(name)
        self.vram_mib = vram_mib


class BarrierEngine(FakeEngine):
    """Blocks until `parties` pages are in flight at once (or times out)."""

    def __init__(self, name: str, barrier: threading.Barrier, vram_mib: int | None = None) -> None:
        super().__init__(name)
        self._barrier = barrier
        if vram_mib is not None:
            self.vram_mib = vram_mib

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        self._barrier.wait(timeout=10)
        return super().analyze_page(image_path, page_index)


def test_engine_vram_mib_reads_declared_value() -> None:
    assert engine_vram_mib(DeclaredVramEngine("primary", 6144)) == 6144


def test_engine_vram_mib_defaults_to_none_when_undeclared() -> None:
    assert engine_vram_mib(FakeEngine("primary")) is None


def _store(tmp_path: Path, page_count: int) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    for index in range(page_count):
        image_path = tmp_path / f"page-{index}.png"
        Image.new("RGB", (100, 150), "white").save(image_path)
        store.add_page(project_id, index, str(image_path), 100, 150)
    return store, project_id


def _run(store: SQLiteStore, project_id: int, tmp_path: Path, primary, secondary, **kwargs):
    return process_book(
        store,
        project_id,
        primary_engine=primary,
        secondary_engine=secondary,
        crops_dir=tmp_path / "crops",
        **kwargs,
    )


def test_memory_budget_caps_effective_concurrency(tmp_path: Path) -> None:
    """6 pages, both engines declare 3 GiB each -> 6 GiB per page.

    A 12 GiB budget allows exactly 2 pages in flight even though max_workers=8
    would allow more. Barriers make the cap observable: a 2-party barrier
    releases (2 pages overlap), a 3-party barrier under the same budget can
    never release, and a wider 18 GiB budget lets the 3-party barrier pass.
    """
    # Budget fits 2 pages: the 2-party barrier releases, all pages succeed.
    store, project_id = _store(tmp_path, 6)
    two_at_once = threading.Barrier(2)
    outcome = _run(
        store,
        project_id,
        tmp_path,
        BarrierEngine("primary", two_at_once, vram_mib=3072),
        BarrierEngine("secondary", two_at_once, vram_mib=3072),
        max_workers=8,
        gpu_memory_mib=12288,
    )
    assert len(outcome.pages) == 6
    assert outcome.failed_pages == ()

    # Same 12 GiB budget but a 3-party barrier: at most 2 pages overlap, so the
    # barrier must break on every page instead of deadlocking the suite.
    store_cap, project_cap = _store(tmp_path / "capped", 6)
    three_capped = threading.Barrier(3)
    capped = _run(
        store_cap,
        project_cap,
        tmp_path / "capped",
        BarrierEngine("primary", three_capped, vram_mib=3072),
        BarrierEngine("secondary", three_capped, vram_mib=3072),
        max_workers=8,
        gpu_memory_mib=12288,
    )
    assert capped.pages == ()
    assert len(capped.failed_pages) == 6

    # A wider budget (18 GiB / 6 GiB = 3 pages) lets the 3-party barrier pass.
    store_wide, project_wide = _store(tmp_path / "wide", 6)
    three_wide = threading.Barrier(3)
    wide = _run(
        store_wide,
        project_wide,
        tmp_path / "wide",
        BarrierEngine("primary", three_wide, vram_mib=3072),
        BarrierEngine("secondary", three_wide, vram_mib=3072),
        max_workers=8,
        gpu_memory_mib=18432,
    )
    assert len(wide.pages) == 6
    assert wide.failed_pages == ()


def test_budget_of_one_page_serializes_pages(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 3)
    barrier = threading.Barrier(2)

    # Each page costs 8 GiB (4 GiB per engine); a 8 GiB budget -> 1 worker.
    # With only one worker the 2-party barrier can never release, so every
    # page fails with a broken barrier instead of deadlocking the suite.
    outcome = _run(
        store,
        project_id,
        tmp_path,
        BarrierEngine("primary", barrier, vram_mib=4096),
        BarrierEngine("secondary", barrier, vram_mib=4096),
        max_workers=4,
        gpu_memory_mib=8192,
    )
    assert outcome.pages == ()
    assert len(outcome.failed_pages) == 3
    for _, message in outcome.failed_pages:
        assert "barrier" in message.lower() or "broken" in message.lower()


def test_undeclared_engines_fall_back_to_default_page_cost(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 2)
    barrier = threading.Barrier(2)

    # Undeclared engines cost DEFAULT_PAGE_VRAM_MIB per page; a budget below
    # that still allows exactly one page -> the 2-party barrier must break.
    outcome = _run(
        store,
        project_id,
        tmp_path,
        BarrierEngine("primary", barrier),
        BarrierEngine("secondary", barrier),
        max_workers=4,
        gpu_memory_mib=DEFAULT_PAGE_VRAM_MIB,
    )
    assert outcome.pages == ()
    assert len(outcome.failed_pages) == 2

    # A budget of 2x the default fits both pages -> a fresh barrier releases.
    store2, project2 = _store(tmp_path / "wide", 2)
    wide_barrier = threading.Barrier(2)
    outcome2 = _run(
        store2,
        project2,
        tmp_path / "wide",
        BarrierEngine("primary", wide_barrier),
        BarrierEngine("secondary", wide_barrier),
        max_workers=4,
        gpu_memory_mib=2 * DEFAULT_PAGE_VRAM_MIB,
    )
    assert len(outcome2.pages) == 2


def test_invalid_gpu_memory_mib_rejected(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 1)
    for bad in (0, -100):
        with pytest.raises(ValueError, match="gpu_memory_mib"):
            _run(
                store,
                project_id,
                tmp_path,
                FakeEngine("primary"),
                FakeEngine("secondary"),
                max_workers=2,
                gpu_memory_mib=bad,
            )


def test_subprocess_engine_declares_vram(tmp_path: Path) -> None:
    from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine

    worker = tmp_path / "w.py"
    worker.write_text("print('{}')", encoding="utf-8")
    engine = SubprocessOCREngine("primary", [sys.executable, str(worker)], vram_mib=5120)
    assert engine.vram_mib == 5120
    assert engine_vram_mib(engine) == 5120

    undeclared = SubprocessOCREngine("secondary", [sys.executable, str(worker)])
    assert undeclared.vram_mib is None
    assert engine_vram_mib(undeclared) is None

    with pytest.raises(ValueError, match="vram_mib"):
        SubprocessOCREngine("bad", [sys.executable, str(worker)], vram_mib=0)


def test_engine_vram_mib_rejects_nonpositive_declaration() -> None:
    class BadEngine(FakeEngine):
        vram_mib = -5

    with pytest.raises(ValueError, match="vram_mib"):
        engine_vram_mib(BadEngine("bad"))


def test_per_page_vram_sums_declared_engines() -> None:
    from scribeforge.domain.scheduling import per_page_vram_mib

    both = (DeclaredVramEngine("a", 2048), DeclaredVramEngine("b", 4096))
    assert per_page_vram_mib(both) == 6144
    mixed = (DeclaredVramEngine("a", 2048), FakeEngine("b"))
    assert per_page_vram_mib(mixed) == DEFAULT_PAGE_VRAM_MIB
    neither = (FakeEngine("a"), FakeEngine("b"))
    assert per_page_vram_mib(neither) == DEFAULT_PAGE_VRAM_MIB


@pytest.mark.parametrize(
    "cpu_workers,budget,per_page,expected_fragment",
    [
        (8, None, 4096, "no GPU memory budget"),
        (8, 16384, 6144, "capped by GPU memory budget"),
        (4, 65536, 4096, "not binding"),
        (8, 6144, 6144, "capped by GPU memory budget"),
        (2, 1024, 6144, "capped by GPU memory budget"),
    ],
)
def test_format_worker_plan_describes_resolution(
    cpu_workers: int, budget: int | None, per_page: int, expected_fragment: str
) -> None:
    from scribeforge.domain.scheduling import format_worker_plan

    text = format_worker_plan(cpu_workers, memory_budget_mib=budget, per_page_vram_mib=per_page)
    assert expected_fragment in text
    assert "page workers:" in text


def test_budget_does_not_change_serial_path(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path, 3)
    outcome = _run(
        store,
        project_id,
        tmp_path,
        FakeEngine("primary", "鸦"),
        FakeEngine("secondary", "雅"),
        max_workers=1,
        gpu_memory_mib=1,  # even a tiny budget keeps serial at 1
    )
    assert [page.page_index for page in outcome.pages] == [0, 1, 2]
    assert outcome.failed_pages == ()


def test_budget_with_failing_page_still_isolates_failures(tmp_path: Path) -> None:
    class FailingOnTwo(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            if page_index == 2:
                raise RuntimeError("boom")
            return super().analyze_page(image_path, page_index)

    store, project_id = _store(tmp_path, 4)
    outcome = _run(
        store,
        project_id,
        tmp_path,
        FailingOnTwo("primary"),
        FakeEngine("secondary", "雅"),
        max_workers=4,
        gpu_memory_mib=2 * DEFAULT_PAGE_VRAM_MIB,
    )
    assert [page.page_index for page in outcome.pages] == [0, 1, 3]
    assert [index for index, _ in outcome.failed_pages] == [2]


def test_budget_cancellation_still_propagates(tmp_path: Path) -> None:
    from scribeforge.domain.cancellation import (
        CancellationToken,
        ProcessingCancelled,
        cancellation_scope,
    )

    store, project_id = _store(tmp_path, 3)
    token = CancellationToken()

    class CancellingEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            token.cancel()
            token.raise_if_cancelled()
            raise AssertionError("unreachable")

    with cancellation_scope(token), pytest.raises(ProcessingCancelled):
        _run(
            store,
            project_id,
            tmp_path,
            CancellingEngine("primary"),
            FakeEngine("secondary", "雅"),
            max_workers=3,
            gpu_memory_mib=DEFAULT_PAGE_VRAM_MIB,
        )


def test_engine_vram_mib_rejects_bool() -> None:
    class BoolEngine(FakeEngine):
        vram_mib = True  # bool is an int subclass but not a real footprint

    with pytest.raises(ValueError, match="vram_mib"):
        engine_vram_mib(BoolEngine("b"))


def test_resolve_page_worker_count_rejects_zero_cpu_workers() -> None:
    with pytest.raises(ValueError, match="cpu_workers"):
        resolve_page_worker_count(0, memory_budget_mib=None, per_page_vram_mib=4096)
