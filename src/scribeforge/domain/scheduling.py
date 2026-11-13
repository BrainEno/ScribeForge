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
from collections.abc import Sequence
from dataclasses import dataclass

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


def estimate_remaining_seconds(
    observed_page_seconds: Sequence[float],
    *,
    remaining_pages: int,
    workers: int,
) -> float:
    """Estimate remaining wall seconds for ``remaining_pages`` at ``workers``.

    Uses the mean of observed per-page seconds; with no observations the
    estimate is 0.0 — an unknown is never invented as a number. ``workers``
    divides the total, so the serial case (workers=1) is the full mean time.
    """
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    if remaining_pages < 0:
        raise ValueError(f"remaining_pages must be >= 0, got {remaining_pages}")
    if not observed_page_seconds or remaining_pages == 0:
        return 0.0
    mean = sum(observed_page_seconds) / len(observed_page_seconds)
    return remaining_pages * mean / workers


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    """Simulated batch schedule for a book under a fixed worker count."""

    waves: int
    pages_per_wave: tuple[int, ...]
    estimated_total_seconds: float
    throughput_pages_per_minute: float


def plan_book_schedule(
    observed_page_seconds: Sequence[float],
    *,
    total_pages: int,
    workers: int,
    per_page_seconds: float | None = None,
) -> SchedulePlan:
    """Simulate how ``total_pages`` finish in waves of ``workers`` pages.

    ``per_page_seconds`` overrides the observed mean when the caller knows the
    per-page cost (e.g. from engine VRAM sizing); otherwise the mean of
    ``observed_page_seconds`` is used. With no cost information the plan is
    empty rather than fabricated.
    """
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    if total_pages < 0:
        raise ValueError(f"total_pages must be >= 0, got {total_pages}")
    if per_page_seconds is not None and per_page_seconds <= 0:
        raise ValueError(f"per_page_seconds must be > 0, got {per_page_seconds}")
    if per_page_seconds is None:
        if not observed_page_seconds:
            return SchedulePlan(0, (), 0.0, 0.0)
        per_page_seconds = sum(observed_page_seconds) / len(observed_page_seconds)
    if total_pages == 0:
        return SchedulePlan(0, (), 0.0, 0.0)
    waves = -(-total_pages // workers)  # ceil division
    pages_per_wave = tuple(min(workers, total_pages - wave * workers) for wave in range(waves))
    total = waves * per_page_seconds
    throughput = (total_pages / (total / 60.0)) if total > 0 else 0.0
    return SchedulePlan(
        waves=waves,
        pages_per_wave=pages_per_wave,
        estimated_total_seconds=round(total, 6),
        throughput_pages_per_minute=round(throughput, 4),
    )


def render_schedule_markdown(plan: SchedulePlan, *, workers: int) -> str:
    """Render a SchedulePlan as a Markdown section for reports."""
    lines = [
        "## Simulated schedule",
        "",
        f"- pages per wave: up to {workers}",
        f"- waves: {plan.waves}",
        f"- estimated total: {plan.estimated_total_seconds:.1f} s",
        f"- throughput: {plan.throughput_pages_per_minute:.2f} pages/min",
        "",
    ]
    if plan.pages_per_wave:
        lines += ["| Wave | Pages |", "| ---: | ---: |"]
        for wave, count in enumerate(plan.pages_per_wave, start=1):
            lines.append(f"| {wave} | {count} |")
        lines.append("")
    return "\n".join(lines)
