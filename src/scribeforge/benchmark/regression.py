"""Regression comparison between two schema-v1 benchmark JSON reports (#62/#68/#90).

``scribeforge benchmark --report-json run.json`` produces a schema-v1 payload;
this module compares two such payloads (a baseline and a current run) metric by
metric — aggregate, per-language, per-direction, vertical reading order and
per-case — and renders a Markdown or JSON regression report with an optional
threshold gate for CI.

N/A semantics match the rest of the benchmark harness: a metric that is
``None`` on either side is never read as 0. It is reported as ``new`` (only in
the current run), ``unavailable`` (null in the current run), ``missing`` (the
whole scope is absent from the current run) or ``na`` (null on both sides),
always with a null delta.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

SCHEMA_VERSION = 1

#: Aggregate metrics compared per engine: (json key, higher_is_better).
AGGREGATE_METRICS: tuple[tuple[str, bool], ...] = (
    ("mean_cer", False),
    ("micro_cer", False),
    ("mean_wer", False),
    ("micro_wer", False),
    ("mean_punctuation_accuracy", True),
    ("reading_order_error_rate", False),
    ("reading_order_errors", False),
    ("missing_lines", False),
    ("extra_lines", False),
    ("elapsed_seconds", False),
    ("pages_per_minute", True),
)

#: Metrics compared inside each ``by_language`` stratum.
LANGUAGE_METRICS: tuple[tuple[str, bool], ...] = (
    ("mean_cer", False),
    ("micro_cer", False),
    ("mean_wer", False),
    ("micro_wer", False),
    ("reading_order_error_rate", False),
)

#: Metrics compared inside each ``by_direction`` stratum.
DIRECTION_METRICS: tuple[tuple[str, bool], ...] = (
    ("mean_cer", False),
    ("reading_order_error_rate", False),
)

#: Metrics compared inside the ``vertical_reading_order`` block.
VERTICAL_METRICS: tuple[tuple[str, bool], ...] = (
    ("mean_cer", False),
    ("reading_order_error_rate", False),
)

#: Metrics compared per case, matched by ``case_id``.
CASE_METRICS: tuple[tuple[str, bool], ...] = (
    ("cer", False),
    ("wer", False),
    ("punctuation_accuracy", True),
    ("reading_order_error_rate", False),
)

#: Integer-valued metrics (rendered without decimals).
_COUNT_METRICS = frozenset({"reading_order_errors", "missing_lines", "extra_lines"})


class RegressionError(Exception):
    """Raised when a report file cannot be loaded or fails schema validation."""


@dataclass(frozen=True)
class RegressionThresholds:
    """Allowed adverse change, in percentage points, per gated metric family."""

    cer_pp: float = 0.5
    wer_pp: float = 1.0
    reading_order_pp: float = 1.0

    def __post_init__(self) -> None:
        for name in ("cer_pp", "wer_pp", "reading_order_pp"):
            if getattr(self, name) < 0:
                raise ValueError(
                    f"thresholds must be non-negative (got {name}={getattr(self, name)})"
                )


@dataclass(frozen=True)
class MetricDelta:
    """One compared metric within one scope (aggregate / stratum / case)."""

    scope: str
    metric: str
    baseline: float | None
    current: float | None
    delta: float | None
    higher_is_better: bool
    status: str  # improved | regressed | unchanged | new | unavailable | missing | na

    @property
    def is_regression(self) -> bool:
        return self.status == "regressed"


@dataclass(frozen=True)
class EngineComparison:
    engine: str
    presence: str  # both | baseline-only | current-only
    deltas: tuple[MetricDelta, ...]


@dataclass(frozen=True)
class RegressionReport:
    baseline_meta: dict[str, str]
    current_meta: dict[str, str]
    comparisons: tuple[EngineComparison, ...]

    def all_deltas(self) -> tuple[MetricDelta, ...]:
        return tuple(delta for comparison in self.comparisons for delta in comparison.deltas)

    def gate_failures(self, thresholds: RegressionThresholds) -> tuple[MetricDelta, ...]:
        """Regressed gated metrics whose adverse change exceeds the threshold.

        Only CER / WER / reading-order error-rate metrics are gated; counts,
        punctuation accuracy and pages-per-minute are reported but never fail
        the gate. N/A values never fail the gate either.
        """

        failures: list[MetricDelta] = []
        for delta in self.all_deltas():
            family = _gate_family(delta.metric)
            if family is None or not delta.is_regression or delta.delta is None:
                continue
            limit = {
                "cer": thresholds.cer_pp,
                "wer": thresholds.wer_pp,
                "ro": thresholds.reading_order_pp,
            }[family]
            if abs(delta.delta) * 100.0 > limit:
                failures.append(delta)
        return tuple(failures)


def _gate_family(metric: str) -> str | None:
    if "cer" in metric:
        return "cer"
    if "wer" in metric:
        return "wer"
    if metric == "reading_order_error_rate":
        return "ro"
    return None


def _meta(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "corpus": str(payload.get("corpus", "?")),
        "manifest_sha256": str(payload.get("manifest_sha256", "?")),
        "generated_at": str(payload.get("generated_at", "?")),
    }


def load_report_json(path: Path) -> dict[str, Any]:
    """Load and schema-validate a benchmark ``--report-json`` file."""

    if not path.is_file():
        raise RegressionError(f"report file not found: {path}")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegressionError(f"{path}: not valid JSON: {exc}") from exc
    _validate_payload(payload, source=str(path))
    return cast("dict[str, Any]", payload)


def _validate_payload(payload: Any, *, source: str) -> None:
    if not isinstance(payload, dict):
        raise RegressionError(f"{source}: top level must be a JSON object")
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise RegressionError(
            f"{source}: unsupported schema_version {version!r} (expected {SCHEMA_VERSION})"
        )
    variants = payload.get("variants")
    if not isinstance(variants, list) or not variants:
        raise RegressionError(f"{source}: 'variants' must be a non-empty list")
    for index, variant in enumerate(variants):
        if not isinstance(variant, dict) or not variant.get("engine"):
            raise RegressionError(f"{source}: variants[{index}] is missing a non-empty 'engine'")


def _make_delta(
    scope: str,
    metric: str,
    baseline: float | None,
    current: float | None,
    *,
    higher_is_better: bool,
) -> MetricDelta:
    if baseline is None and current is None:
        status = "na"
    elif baseline is None:
        status = "new"
    elif current is None:
        status = "unavailable"
    else:
        delta_value = current - baseline
        if higher_is_better:
            status = (
                "regressed" if delta_value < 0 else ("improved" if delta_value > 0 else "unchanged")
            )
        else:
            status = (
                "regressed" if delta_value > 0 else ("improved" if delta_value < 0 else "unchanged")
            )
        return MetricDelta(
            scope=scope,
            metric=metric,
            baseline=baseline,
            current=current,
            delta=delta_value,
            higher_is_better=higher_is_better,
            status=status,
        )
    return MetricDelta(
        scope=scope,
        metric=metric,
        baseline=baseline,
        current=current,
        delta=None,
        higher_is_better=higher_is_better,
        status=status,
    )


def _compare_metric_dicts(
    scope_prefix: str,
    metrics: Sequence[tuple[str, bool]],
    baseline_rows: dict[str, dict[str, Any]],
    current_rows: dict[str, dict[str, Any]],
    key_field: str,
) -> list[MetricDelta]:
    deltas: list[MetricDelta] = []
    for key in sorted(set(baseline_rows) | set(current_rows)):
        scope = f"{scope_prefix}:{key}"
        base_row = baseline_rows.get(key)
        cur_row = current_rows.get(key)
        for metric, higher_is_better in metrics:
            base_val = base_row.get(metric) if base_row is not None else None
            cur_val = cur_row.get(metric) if cur_row is not None else None
            if base_row is None or cur_row is None:
                status = "new" if base_row is None else "missing"
                deltas.append(
                    MetricDelta(
                        scope=scope,
                        metric=metric,
                        baseline=base_val if base_row is not None else None,
                        current=cur_val if cur_row is not None else None,
                        delta=None,
                        higher_is_better=higher_is_better,
                        status=status,
                    )
                )
            else:
                deltas.append(
                    _make_delta(
                        scope,
                        metric,
                        _as_float(base_val),
                        _as_float(cur_val),
                        higher_is_better=higher_is_better,
                    )
                )
    return deltas


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _compare_variant(
    baseline_variant: dict[str, Any], current_variant: dict[str, Any]
) -> tuple[MetricDelta, ...]:
    deltas: list[MetricDelta] = []

    base_agg = baseline_variant.get("aggregate") or {}
    cur_agg = current_variant.get("aggregate") or {}
    for metric, higher_is_better in AGGREGATE_METRICS:
        deltas.append(
            _make_delta(
                "aggregate",
                metric,
                _as_float(base_agg.get(metric)),
                _as_float(cur_agg.get(metric)),
                higher_is_better=higher_is_better,
            )
        )

    deltas.extend(
        _compare_metric_dicts(
            "language",
            LANGUAGE_METRICS,
            {row["language"]: row for row in baseline_variant.get("by_language") or []},
            {row["language"]: row for row in current_variant.get("by_language") or []},
            "language",
        )
    )
    deltas.extend(
        _compare_metric_dicts(
            "direction",
            DIRECTION_METRICS,
            {row["direction"]: row for row in baseline_variant.get("by_direction") or []},
            {row["direction"]: row for row in current_variant.get("by_direction") or []},
            "direction",
        )
    )

    base_vertical = (baseline_variant.get("vertical_reading_order") or [None])[0]
    cur_vertical = (current_variant.get("vertical_reading_order") or [None])[0]
    for metric, higher_is_better in VERTICAL_METRICS:
        if base_vertical is None and cur_vertical is None:
            continue
        if base_vertical is None or cur_vertical is None:
            status = "new" if base_vertical is None else "missing"
            deltas.append(
                MetricDelta(
                    scope="vertical_reading_order",
                    metric=metric,
                    baseline=None
                    if base_vertical is None
                    else _as_float(base_vertical.get(metric)),
                    current=None if cur_vertical is None else _as_float(cur_vertical.get(metric)),
                    delta=None,
                    higher_is_better=higher_is_better,
                    status=status,
                )
            )
        else:
            deltas.append(
                _make_delta(
                    "vertical_reading_order",
                    metric,
                    _as_float(base_vertical.get(metric)),
                    _as_float(cur_vertical.get(metric)),
                    higher_is_better=higher_is_better,
                )
            )

    deltas.extend(
        _compare_metric_dicts(
            "case",
            CASE_METRICS,
            {row["case_id"]: row for row in baseline_variant.get("cases") or []},
            {row["case_id"]: row for row in current_variant.get("cases") or []},
            "case_id",
        )
    )
    return tuple(deltas)


def compare_payloads(baseline: dict[str, Any], current: dict[str, Any]) -> RegressionReport:
    """Compare two schema-v1 benchmark report payloads."""

    _validate_payload(baseline, source="baseline")
    _validate_payload(current, source="current")

    base_variants = {variant["engine"]: variant for variant in baseline["variants"]}
    cur_variants = {variant["engine"]: variant for variant in current["variants"]}

    comparisons: list[EngineComparison] = []
    for engine in list(base_variants) + [e for e in cur_variants if e not in base_variants]:
        base_variant = base_variants.get(engine)
        cur_variant = cur_variants.get(engine)
        if base_variant is not None and cur_variant is not None:
            comparisons.append(
                EngineComparison(
                    engine=engine,
                    presence="both",
                    deltas=_compare_variant(base_variant, cur_variant),
                )
            )
        else:
            comparisons.append(
                EngineComparison(
                    engine=engine,
                    presence="baseline-only" if base_variant is not None else "current-only",
                    deltas=(),
                )
            )
    return RegressionReport(
        baseline_meta=_meta(baseline),
        current_meta=_meta(current),
        comparisons=tuple(comparisons),
    )


def _fmt_value(metric: str, value: float | None) -> str:
    if value is None:
        return "N/A"
    if metric in _COUNT_METRICS:
        return str(int(value))
    return f"{value:.4f}"


def _fmt_delta(metric: str, delta: float | None) -> str:
    if delta is None:
        return "N/A"
    if metric in _COUNT_METRICS:
        return f"{int(delta):+d}"
    return f"{delta:+.4f}"


def render_regression_markdown(
    report: RegressionReport, thresholds: RegressionThresholds | None = None
) -> str:
    """Render the comparison as a Markdown regression report."""

    thresholds = thresholds or RegressionThresholds()
    failures = report.gate_failures(thresholds)
    lines: list[str] = [
        "# Benchmark Regression Comparison",
        "",
        (
            f"- baseline: corpus `{report.baseline_meta['corpus']}` · "
            f"manifest sha256 `{report.baseline_meta['manifest_sha256']}` · "
            f"generated {report.baseline_meta['generated_at']}"
        ),
        (
            f"- current: corpus `{report.current_meta['corpus']}` · "
            f"manifest sha256 `{report.current_meta['manifest_sha256']}` · "
            f"generated {report.current_meta['generated_at']}"
        ),
        f"- engines compared: {len(report.comparisons)}",
        (
            f"- thresholds: cer ≤ {thresholds.cer_pp:.2f}pp · wer ≤ {thresholds.wer_pp:.2f}pp · "
            f"reading-order ≤ {thresholds.reading_order_pp:.2f}pp"
        ),
        "",
    ]
    for comparison in report.comparisons:
        lines.append(f"## Engine: {comparison.engine} ({comparison.presence})")
        lines.append("")
        if not comparison.deltas:
            lines.append("Not present in both reports; no metric deltas.")
            lines.append("")
            continue
        lines.append("| scope | metric | baseline | current | delta | verdict |")
        lines.append("|---|---|---|---|---|---|")
        for delta in comparison.deltas:
            lines.append(
                f"| {delta.scope} | {delta.metric} | {_fmt_value(delta.metric, delta.baseline)} "
                f"| {_fmt_value(delta.metric, delta.current)} "
                f"| {_fmt_delta(delta.metric, delta.delta)} | {delta.status} |"
            )
        lines.append("")
    lines.append(f"## Gate: {'FAIL' if failures else 'PASS'}")
    lines.append("")
    lines.append("## Gate failures")
    lines.append("")
    if not failures:
        lines.append("- none")
    else:
        for delta in failures:
            family = _gate_family(delta.metric) or "?"
            limit = {
                "cer": thresholds.cer_pp,
                "wer": thresholds.wer_pp,
                "ro": thresholds.reading_order_pp,
            }[family]
            lines.append(
                f"- `{delta.scope}` {delta.metric}: "
                f"{_fmt_value(delta.metric, delta.baseline)} -> "
                f"{_fmt_value(delta.metric, delta.current)} "
                f"({_fmt_delta(delta.metric, delta.delta)}, "
                f"{abs(delta.delta or 0.0) * 100.0:.2f}pp > {limit:.2f}pp)"
            )
    lines.append("")
    return "\n".join(lines)


def regression_json(
    report: RegressionReport, thresholds: RegressionThresholds | None = None
) -> str:
    """Render the comparison as deterministic schema-v1 JSON."""

    thresholds = thresholds or RegressionThresholds()
    failures = report.gate_failures(thresholds)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "benchmark-regression",
        "baseline": report.baseline_meta,
        "current": report.current_meta,
        "thresholds": asdict(thresholds),
        "gate": {
            "passed": not failures,
            "failures": [
                {
                    "scope": delta.scope,
                    "metric": delta.metric,
                    "baseline": delta.baseline,
                    "current": delta.current,
                    "delta": delta.delta,
                }
                for delta in failures
            ],
        },
        "engines": [
            {
                "engine": comparison.engine,
                "presence": comparison.presence,
                "deltas": [
                    {
                        "scope": delta.scope,
                        "metric": delta.metric,
                        "baseline": delta.baseline,
                        "current": delta.current,
                        "delta": delta.delta,
                        "higher_is_better": delta.higher_is_better,
                        "status": delta.status,
                    }
                    for delta in comparison.deltas
                ],
            }
            for comparison in report.comparisons
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
