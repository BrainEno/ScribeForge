"""Scheduling estimate tests (issue #70).

Page-level parallelism is only useful if operators can predict the win, so the
scheduler exposes pure estimators: from observed per-page seconds and a worker
count, estimate the remaining wall time of a book, and simulate the batch
schedule (which pages finish when) without running anything.
"""

from __future__ import annotations

import pytest

from scribeforge.domain.scheduling import (
    estimate_remaining_seconds,
    plan_book_schedule,
)


@pytest.mark.parametrize(
    "observed,remaining,workers,expected",
    [
        ([2.0, 4.0], 10, 2, 15.0),  # mean 3s, 10 pages, 2 workers -> 30/2
        ([2.0, 4.0], 10, 1, 30.0),  # serial: full mean time
        ([2.0, 4.0], 0, 4, 0.0),  # nothing remaining
        ([], 10, 4, 0.0),  # no observations: unknown -> 0 (never invent)
        ([6.0], 7, 3, 14.0),  # single observation still works
    ],
)
def test_estimate_remaining_seconds(observed, remaining, workers, expected) -> None:
    assert estimate_remaining_seconds(
        observed, remaining_pages=remaining, workers=workers
    ) == pytest.approx(expected)


@pytest.mark.parametrize("bad_workers", [0, -1])
def test_estimate_rejects_bad_workers(bad_workers: int) -> None:
    with pytest.raises(ValueError, match="workers"):
        estimate_remaining_seconds([1.0], remaining_pages=5, workers=bad_workers)


def test_estimate_rejects_negative_remaining() -> None:
    with pytest.raises(ValueError, match="remaining_pages"):
        estimate_remaining_seconds([1.0], remaining_pages=-1, workers=2)


def test_plan_book_schedule_batches() -> None:
    plan = plan_book_schedule([2.0, 2.0, 2.0, 2.0], total_pages=4, workers=2, per_page_seconds=2.0)
    # 4 pages, 2 workers, 2s each -> two waves of 2s
    assert plan.waves == 2
    assert plan.estimated_total_seconds == pytest.approx(4.0)
    assert plan.pages_per_wave == (2, 2)
    assert plan.throughput_pages_per_minute == pytest.approx(60.0)


def test_plan_book_schedule_serial() -> None:
    plan = plan_book_schedule([1.0], total_pages=3, workers=1, per_page_seconds=1.0)
    assert plan.waves == 3
    assert plan.estimated_total_seconds == pytest.approx(3.0)
    assert plan.pages_per_wave == (1, 1, 1)


def test_plan_book_schedule_falls_back_to_observed_mean() -> None:
    plan = plan_book_schedule([3.0, 5.0], total_pages=4, workers=2)
    # mean 4s per page, 4 pages, 2 workers -> 2 waves of 4s
    assert plan.estimated_total_seconds == pytest.approx(8.0)
    assert plan.waves == 2


def test_plan_book_schedule_empty() -> None:
    plan = plan_book_schedule([], total_pages=0, workers=4)
    assert plan.waves == 0
    assert plan.estimated_total_seconds == 0.0
    assert plan.throughput_pages_per_minute == 0.0


def test_plan_book_schedule_validates() -> None:
    with pytest.raises(ValueError, match="workers"):
        plan_book_schedule([], total_pages=1, workers=0)
    with pytest.raises(ValueError, match="total_pages"):
        plan_book_schedule([], total_pages=-1, workers=1)
    with pytest.raises(ValueError, match="per_page_seconds"):
        plan_book_schedule([], total_pages=1, workers=1, per_page_seconds=0)


def test_plan_fewer_pages_than_workers() -> None:
    plan = plan_book_schedule([], total_pages=2, workers=8, per_page_seconds=3.0)
    assert plan.waves == 1
    assert plan.pages_per_wave == (2,)
    assert plan.estimated_total_seconds == pytest.approx(3.0)
    assert plan.throughput_pages_per_minute == pytest.approx(40.0)


def test_plan_exact_multiple_of_workers() -> None:
    plan = plan_book_schedule([], total_pages=6, workers=3, per_page_seconds=2.0)
    assert plan.pages_per_wave == (3, 3)
    assert plan.waves == 2


def test_plan_per_page_override_beats_observed() -> None:
    plan = plan_book_schedule([1.0, 1.0], total_pages=4, workers=2, per_page_seconds=10.0)
    assert plan.estimated_total_seconds == pytest.approx(20.0)


def test_estimate_matches_plan_total_for_full_book() -> None:
    observed = [2.0, 4.0]  # mean 3
    est = estimate_remaining_seconds(observed, remaining_pages=8, workers=2)
    plan = plan_book_schedule(observed, total_pages=8, workers=2)
    assert est == pytest.approx(plan.estimated_total_seconds)


def test_estimate_zero_observations_never_invents() -> None:
    assert estimate_remaining_seconds([], remaining_pages=100, workers=4) == 0.0


def test_plan_zero_pages_with_observations() -> None:
    plan = plan_book_schedule([1.0], total_pages=0, workers=2)
    assert plan.waves == 0
    assert plan.estimated_total_seconds == 0.0


def test_plan_throughput_serial() -> None:
    plan = plan_book_schedule([], total_pages=10, workers=1, per_page_seconds=6.0)
    assert plan.throughput_pages_per_minute == pytest.approx(10.0)


def test_estimate_workers_higher_than_pages() -> None:
    # 2 pages at 8 workers: wall time is one page, not divided below one page
    est = estimate_remaining_seconds([4.0], remaining_pages=2, workers=8)
    assert est == pytest.approx(1.0)


def test_render_schedule_markdown_waves_table() -> None:
    from scribeforge.domain.scheduling import render_schedule_markdown

    plan = plan_book_schedule([], total_pages=5, workers=2, per_page_seconds=2.0)
    text = render_schedule_markdown(plan, workers=2)
    assert "## Simulated schedule" in text
    assert "| 1 | 2 |" in text
    assert "| 3 | 1 |" in text
    assert "waves: 3" in text
    assert "estimated total: 6.0 s" in text


def test_render_schedule_markdown_empty_plan() -> None:
    from scribeforge.domain.scheduling import render_schedule_markdown

    text = render_schedule_markdown(plan_book_schedule([], total_pages=0, workers=4), workers=4)
    assert "waves: 0" in text
    assert "| Wave |" not in text


def test_plan_wave_sizes_sum_to_total() -> None:
    for total in (1, 2, 7, 100):
        for workers in (1, 3, 8):
            plan = plan_book_schedule([], total_pages=total, workers=workers, per_page_seconds=1.0)
            assert sum(plan.pages_per_wave) == total
            assert all(1 <= w <= workers for w in plan.pages_per_wave)


@pytest.mark.parametrize("total,workers", [(1, 1), (5, 2), (9, 4), (16, 16), (17, 8), (100, 7)])
def test_plan_waves_and_wave_sizes(total: int, workers: int) -> None:
    plan = plan_book_schedule([], total_pages=total, workers=workers, per_page_seconds=1.0)
    expected_waves = -(-total // workers)
    assert plan.waves == expected_waves
    assert len(plan.pages_per_wave) == expected_waves
    assert plan.pages_per_wave[-1] == total - workers * (expected_waves - 1)
    assert plan.estimated_total_seconds == pytest.approx(expected_waves * 1.0)


@pytest.mark.parametrize("remaining,workers", [(0, 1), (1, 4), (10, 3), (50, 8)])
def test_estimate_scales_linearly_with_remaining(remaining: int, workers: int) -> None:
    observed = [2.0, 6.0]  # mean 4
    est = estimate_remaining_seconds(observed, remaining_pages=remaining, workers=workers)
    assert est == pytest.approx(remaining * 4.0 / workers)


def test_estimate_is_monotonic_in_workers() -> None:
    observed = [3.0]
    estimates = [
        estimate_remaining_seconds(observed, remaining_pages=10, workers=w) for w in (1, 2, 4, 8)
    ]
    assert estimates == sorted(estimates, reverse=True)


def test_render_schedule_throughput_line() -> None:
    from scribeforge.domain.scheduling import render_schedule_markdown

    plan = plan_book_schedule([], total_pages=120, workers=4, per_page_seconds=2.0)
    text = render_schedule_markdown(plan, workers=4)
    # 30 waves x 2s = 60s for 120 pages -> 120 pages/min
    assert "throughput: 120.00 pages/min" in text
    assert "waves: 30" in text
