"""Benchmark trend history over repeated runs (#62/#68/#90).

``scribeforge benchmark --history runs.jsonl`` appends every scored run's
schema-v1 report JSON as one line of a JSONL history file.
``scribeforge benchmark-trend runs.jsonl`` then extracts per-engine metric
series (aggregate or a stratum scope) across those runs and flags degradation:
a metric whose last ``--window`` deltas are all adverse, beyond
``--max-adverse-pp`` in total, fails the ``--fail-on-trend`` gate (exit 3).

N/A semantics match the rest of the harness: a ``None`` value is skipped when
computing deltas — it is never read as 0 and never counted as adverse.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

from scribeforge.benchmark.regression import (
    AGGREGATE_METRICS,
    CASE_METRICS,
    DIRECTION_METRICS,
    LANGUAGE_METRICS,
    VERTICAL_METRICS,
    validate_report_payload,
)

SCHEMA_VERSION = 1

#: metric name -> higher_is_better, merged from the regression metric tables.
_HIGHER_IS_BETTER: dict[str, bool] = {
    metric: higher_is_better
    for table in (
        AGGREGATE_METRICS,
        LANGUAGE_METRICS,
        DIRECTION_METRICS,
        VERTICAL_METRICS,
        CASE_METRICS,
    )
    for metric, higher_is_better in table
}

#: Metrics shown by default when ``benchmark-trend`` gets no ``--metric``.
DEFAULT_METRICS: tuple[str, ...] = ("mean_cer", "mean_wer", "reading_order_error_rate")


class HistoryError(Exception):
    """Raised when a history file contains an invalid line."""


@dataclass(frozen=True)
class TrendPoint:
    generated_at: str
    manifest_sha256: str
    value: float | None


@dataclass(frozen=True)
class TrendSeries:
    engine: str
    scope: str
    metric: str
    points: tuple[TrendPoint, ...]

    @property
    def higher_is_better(self) -> bool:
        return _HIGHER_IS_BETTER.get(self.metric, False)


@dataclass(frozen=True)
class TrendAnalysis:
    series: TrendSeries
    deltas: tuple[float, ...]
    consecutive_adverse: int
    window_adverse_pp: float
    status: str  # degrading | stable | insufficient-data
    gate_fail: bool


def load_history(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL benchmark history; a missing file is an empty history."""

    if not path.is_file():
        return []
    runs: list[dict[str, Any]] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HistoryError(f"{path}: line {lineno}: not valid JSON: {exc}") from exc
        try:
            validate_report_payload(payload, source=f"{path}: line {lineno}")
        except Exception as exc:
            raise HistoryError(f"{path}: line {lineno}: {exc}") from exc
        runs.append(payload)
    return runs


def append_history(path: Path, payload: dict[str, Any]) -> None:
    """Append one schema-v1 report payload as a single compact JSON line."""

    validate_report_payload(payload, source=str(path))
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _scope_value(variant: dict[str, Any], scope: str, metric: str) -> tuple[bool, float | None]:
    """Return (present, value) for a scope inside one variant."""

    if scope == "aggregate":
        aggregate = variant.get("aggregate") or {}
        return True, _as_float(aggregate.get(metric))
    if scope.startswith("language:"):
        language = scope.split(":", 1)[1]
        for row in variant.get("by_language") or []:
            if row.get("language") == language:
                return True, _as_float(row.get(metric))
        return False, None
    if scope.startswith("direction:"):
        direction = scope.split(":", 1)[1]
        for row in variant.get("by_direction") or []:
            if row.get("direction") == direction:
                return True, _as_float(row.get(metric))
        return False, None
    if scope.startswith("case:"):
        case_id = scope.split(":", 1)[1]
        for row in variant.get("cases") or []:
            if row.get("case_id") == case_id:
                return True, _as_float(row.get(metric))
        return False, None
    if scope == "vertical_reading_order":
        rows = variant.get("vertical_reading_order") or []
        if not rows:
            return False, None
        return True, _as_float(rows[0].get(metric))
    raise HistoryError(f"unknown scope: {scope!r}")


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def extract_series(
    history: Sequence[dict[str, Any]], *, engine: str, scope: str, metric: str
) -> TrendSeries:
    """Collect one metric's values across all runs for one engine."""

    points: list[TrendPoint] = []
    for payload in history:
        variant = next((v for v in payload["variants"] if v.get("engine") == engine), None)
        if variant is None:
            continue
        present, value = _scope_value(variant, scope, metric)
        if not present:
            continue
        points.append(
            TrendPoint(
                generated_at=str(payload.get("generated_at", "?")),
                manifest_sha256=str(payload.get("manifest_sha256", "?")),
                value=value,
            )
        )
    return TrendSeries(engine=engine, scope=scope, metric=metric, points=tuple(points))


def _is_adverse(delta: float, *, higher_is_better: bool) -> bool:
    return delta < 0 if higher_is_better else delta > 0


def analyze_series(series: TrendSeries, *, window: int, max_adverse_pp: float) -> TrendAnalysis:
    """Analyze consecutive deltas; gate on the adverse tail of ``window`` runs."""

    if window < 1:
        raise ValueError("window must be >= 1")
    # Bridge over N/A points: compare consecutive *measured* values; a None is
    # never read as 0 and never contributes a delta.
    measured = [point.value for point in series.points if point.value is not None]
    deltas: list[float] = [cur - prev for prev, cur in pairwise(measured)]

    consecutive_adverse = 0
    for delta in reversed(deltas):
        if delta != 0 and _is_adverse(delta, higher_is_better=series.higher_is_better):
            consecutive_adverse += 1
        else:
            break

    if len(deltas) < window:
        status = "insufficient-data"
    elif consecutive_adverse >= window:
        status = "degrading"
    else:
        status = "stable"

    tail = deltas[len(deltas) - consecutive_adverse :] if consecutive_adverse else []
    window_adverse_pp = sum(abs(d) for d in tail) * 100.0
    gate_fail = status == "degrading" and window_adverse_pp > max_adverse_pp
    return TrendAnalysis(
        series=series,
        deltas=tuple(deltas),
        consecutive_adverse=consecutive_adverse,
        window_adverse_pp=window_adverse_pp,
        status=status,
        gate_fail=gate_fail,
    )


def _fmt_value(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.4f}"


def _fmt_delta(delta: float | None) -> str:
    return "—" if delta is None else f"{delta:+.4f}"


def render_trend_markdown(
    analyses: Sequence[TrendAnalysis],
    *,
    history: str,
    run_count: int,
    window: int,
    max_adverse_pp: float,
) -> str:
    """Render the trend analyses as Markdown."""

    lines: list[str] = [
        "# Benchmark Trend",
        "",
        f"- history: `{history}`",
        f"- runs: {run_count}",
        f"- window: {window} · max adverse per window: {max_adverse_pp:.2f}pp",
        "",
    ]
    if not analyses:
        lines.append("No series to analyze (empty history or no matching engine/scope).")
        lines.append("")
        return "\n".join(lines)
    for analysis in analyses:
        series = analysis.series
        direction = "higher is better" if series.higher_is_better else "lower is better"
        lines.append(
            f"## engine={series.engine} scope={series.scope} metric={series.metric} ({direction})"
        )
        lines.append("")
        lines.append("| run | generated | manifest | value | delta |")
        lines.append("|---|---|---|---|---|")
        previous: float | None = None
        for index, point in enumerate(series.points, start=1):
            delta = None if previous is None or point.value is None else point.value - previous
            lines.append(
                f"| {index} | {point.generated_at} | {point.manifest_sha256} "
                f"| {_fmt_value(point.value)} | {_fmt_delta(delta)} |"
            )
            if point.value is not None:
                previous = point.value
        lines.append("")
        if analysis.status == "degrading":
            lines.append(
                f"verdict: degrading ({analysis.consecutive_adverse} consecutive adverse, "
                f"+{analysis.window_adverse_pp:.2f}pp > {max_adverse_pp:.2f}pp)"
            )
        elif analysis.status == "insufficient-data":
            lines.append(
                f"verdict: insufficient-data ({len(analysis.deltas)} delta(s) < window {window})"
            )
        else:
            lines.append("verdict: stable")
        lines.append("")
    return "\n".join(lines)


def trend_json(
    analyses: Sequence[TrendAnalysis],
    *,
    history: str,
    run_count: int,
    window: int,
    max_adverse_pp: float,
) -> str:
    """Render the trend analyses as deterministic schema-v1 JSON."""

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "benchmark-trend",
        "history": history,
        "run_count": run_count,
        "window": window,
        "max_adverse_pp": max_adverse_pp,
        "series": [
            {
                "engine": analysis.series.engine,
                "scope": analysis.series.scope,
                "metric": analysis.series.metric,
                "higher_is_better": analysis.series.higher_is_better,
                "status": analysis.status,
                "gate_fail": analysis.gate_fail,
                "consecutive_adverse": analysis.consecutive_adverse,
                "window_adverse_pp": analysis.window_adverse_pp,
                "points": [
                    {
                        "generated_at": point.generated_at,
                        "manifest_sha256": point.manifest_sha256,
                        "value": point.value,
                    }
                    for point in analysis.series.points
                ],
                "deltas": list(analysis.deltas),
            }
            for analysis in analyses
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
