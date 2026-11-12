"""Concurrency scheduling under a GPU memory budget (issue #70).

Page-level parallelism is I/O-bound and scales with CPU cores, but on GPU
hosts every in-flight page keeps its OCR engines resident in VRAM. Running
``cpu_workers`` pages at once can therefore exhaust the card. These helpers
cap the page worker count by a memory budget: each in-flight page costs the
sum of its engines' declared VRAM (or a conservative default), and the worker
count is the largest number of pages that fits, never exceeding the CPU-based
count and never dropping below one.
"""

from __future__ import annotations

import os

#: Conservative per-page VRAM cost (MiB) used when no engine declares its own
#: footprint. Sized for a typical layout-detection model plus OCR backbone on
#: a single page; real engines should declare ``vram_mib`` instead.
DEFAULT_PAGE_VRAM_MIB = 6144


def engine_vram_mib(engine: object) -> int | None:
    """Return the engine's declared VRAM footprint in MiB, if it declares one."""
    value = getattr(engine, "vram_mib", None)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"engine vram_mib must be a positive int, got {value!r}")
    return value


def per_page_vram_mib(
    engines: tuple[object, ...], *, fallback_mib: int = DEFAULT_PAGE_VRAM_MIB
) -> int:
    """Total VRAM one in-flight page needs: declared engine footprints, or the fallback."""
    declared = [engine_vram_mib(engine) for engine in engines]
    if any(cost is None for cost in declared):
        return fallback_mib
    return sum(cost for cost in declared if cost is not None)


def resolve_page_worker_count(
    cpu_workers: int,
    *,
    memory_budget_mib: int | None,
    per_page_vram_mib: int,
) -> int:
    """Largest page worker count that fits the budget, clamped to [1, cpu_workers].

    ``memory_budget_mib=None`` disables the cap (CPU-bound scheduling only).
    The result is always at least one: a page must be able to run even when the
    budget is smaller than a single page's cost, otherwise the book can never
    be processed at all.
    """
    if cpu_workers < 1:
        raise ValueError(f"cpu_workers must be >= 1, got {cpu_workers}")
    if per_page_vram_mib < 1:
        raise ValueError(f"per_page_vram_mib must be >= 1, got {per_page_vram_mib}")
    if memory_budget_mib is None:
        return cpu_workers
    if memory_budget_mib < 1:
        raise ValueError(f"memory_budget_mib must be >= 1 when set, got {memory_budget_mib}")
    affordable = memory_budget_mib // per_page_vram_mib
    return max(1, min(cpu_workers, affordable))


def format_worker_plan(
    cpu_workers: int,
    *,
    memory_budget_mib: int | None,
    per_page_vram_mib: int,
) -> str:
    """Human-readable summary of how the page worker count was resolved.

    Used by the CLI so a GPU-budget run explains its own concurrency cap.
    """
    workers = resolve_page_worker_count(
        cpu_workers,
        memory_budget_mib=memory_budget_mib,
        per_page_vram_mib=per_page_vram_mib,
    )
    if memory_budget_mib is None:
        return f"page workers: {workers} (CPU-based; no GPU memory budget set)"
    affordable = memory_budget_mib // per_page_vram_mib
    capped = workers < cpu_workers
    reason = (
        f"capped by GPU memory budget ({memory_budget_mib} MiB / "
        f"{per_page_vram_mib} MiB per page = {affordable} affordable)"
        if capped
        else "GPU memory budget not binding"
    )
    return f"page workers: {workers} of {cpu_workers} CPU-based; {reason}"


def resolve_cpu_workers(max_workers: int) -> int:
    """Normalize the requested worker count; 0 means "half the CPU cores"."""
    if max_workers < 0:
        raise ValueError(f"max_workers must be >= 0, got {max_workers}")
    if max_workers == 0:
        return max(1, (os.cpu_count() or 1) // 2)
    return max_workers
