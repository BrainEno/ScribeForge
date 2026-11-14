"""Benchmark trend history: `benchmark --history` + `benchmark-trend` (#62/#68/#90).

Each scored run can append its schema-v1 report JSON as one line of a JSONL
history; `benchmark-trend` then renders per-engine metric trajectories and can
fail CI when a metric degrades for `--window` consecutive runs. N/A values are
skipped (never read as 0, never counted as adverse).
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scribeforge.benchmark.report import render_report_json
from scribeforge.benchmark.runner import (
    BenchmarkReport,
    CaseScore,
    aggregate_case_scores,
)
from scribeforge.benchmark.trend import (
    HistoryError,
    TrendSeries,
    analyze_series,
    append_history,
    extract_series,
    load_history,
    render_trend_markdown,
    trend_json,
)


def _case(
    case_id: str = "case-1",
    *,
    language: str = "en",
    direction: str | None = None,
    cer: float = 0.10,
    wer: float | None = 0.20,
    reading_order_error_rate: float = 0.0,
) -> CaseScore:
    return CaseScore(
        case_id=case_id,
        language=language,
        tags=("body",),
        direction=direction,
        cer=cer,
        wer=wer,
        punctuation_accuracy=0.9,
        missing_lines=0,
        extra_lines=0,
        reading_order_errors=int(reading_order_error_rate * 10),
        reading_order_error_rate=reading_order_error_rate,
        elapsed_seconds=2.0,
    )


def _run_payload(
    *cases: CaseScore,
    engine: str = "mineru",
    generated_at: str = "2026-11-01T00:00:00Z",
    pages_per_minute: float = 30.0,
) -> dict:
    aggregate = replace(
        aggregate_case_scores(cases), elapsed_seconds=2.0, pages_per_minute=pages_per_minute
    )
    report = BenchmarkReport(engine=engine, cases=cases, aggregate=aggregate)
    return json.loads(
        render_report_json(
            [report],
            corpus="corpus/manifest.json",
            manifest_sha256="abc123",
            generated_at=generated_at,
        )
    )


def _history_file(tmp_path: Path, *payloads: dict, name: str = "history.jsonl") -> Path:
    path = tmp_path / name
    for payload in payloads:
        append_history(path, payload)
    return path


# ---------------------------------------------------------------------------
# history file I/O
# ---------------------------------------------------------------------------


def test_load_history_reads_jsonl_and_skips_blank_lines(tmp_path: Path) -> None:
    path = _history_file(
        tmp_path, _run_payload(), _run_payload(generated_at="2026-11-02T00:00:00Z")
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    history = load_history(path)
    assert len(history) == 2
    assert history[0]["generated_at"] == "2026-11-01T00:00:00Z"


def test_load_history_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_history(tmp_path / "nope.jsonl") == []


def test_load_history_rejects_invalid_json_line(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    valid_line = json.dumps(_run_payload(), sort_keys=True, separators=(",", ":"))
    path.write_text(valid_line + "\n{oops\n", encoding="utf-8")
    with pytest.raises(HistoryError, match="line 2"):
        load_history(path)


def test_load_history_rejects_wrong_schema(tmp_path: Path) -> None:
    payload = _run_payload()
    payload["schema_version"] = 7
    path = tmp_path / "h.jsonl"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    with pytest.raises(HistoryError, match="schema_version"):
        load_history(path)


def test_append_history_appends_one_compact_line_and_roundtrips(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    append_history(path, _run_payload())
    append_history(path, _run_payload(generated_at="2026-11-03T00:00:00Z"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "\n" not in lines[0].strip()
    assert json.loads(lines[1])["generated_at"] == "2026-11-03T00:00:00Z"


# ---------------------------------------------------------------------------
# series extraction
# ---------------------------------------------------------------------------


def test_extract_series_aggregate_in_run_order_with_none_preserved(tmp_path: Path) -> None:
    path = _history_file(
        tmp_path,
        _run_payload(_case(cer=0.10, wer=0.20)),
        _run_payload(_case(cer=0.12, wer=None), generated_at="2026-11-02T00:00:00Z"),
        _run_payload(_case(cer=0.11, wer=0.18), generated_at="2026-11-03T00:00:00Z"),
    )
    series = extract_series(
        load_history(path), engine="mineru", scope="aggregate", metric="mean_wer"
    )
    values = [point.value for point in series.points]
    assert values == [0.20, None, 0.18]  # None stays None, never 0
    assert series.metric == "mean_wer"
    assert series.higher_is_better is False


def test_extract_series_language_scope(tmp_path: Path) -> None:
    path = _history_file(
        tmp_path,
        _run_payload(_case("zh-1", language="zh", cer=0.10)),
        _run_payload(_case("zh-1", language="zh", cer=0.20), generated_at="2026-11-02T00:00:00Z"),
    )
    series = extract_series(
        load_history(path), engine="mineru", scope="language:zh", metric="mean_cer"
    )
    assert [p.value for p in series.points] == [0.10, 0.20]


def test_extract_series_unknown_engine_or_missing_scope_is_empty(tmp_path: Path) -> None:
    path = _history_file(tmp_path, _run_payload())
    history = load_history(path)
    assert (
        extract_series(history, engine="surya", scope="aggregate", metric="mean_cer").points == ()
    )
    assert extract_series(
        history, engine="mineru", scope="language:ja", metric="mean_cer"
    ).points == (())


# ---------------------------------------------------------------------------
# analysis + gate
# ---------------------------------------------------------------------------


def _series(
    values: list[float | None], metric: str = "mean_cer", scope: str = "aggregate"
) -> TrendSeries:
    from scribeforge.benchmark.trend import TrendPoint

    points = tuple(
        TrendPoint(generated_at=f"2026-11-0{i + 1}T00:00:00Z", manifest_sha256="abc123", value=v)
        for i, v in enumerate(values)
    )
    return TrendSeries(engine="mineru", scope=scope, metric=metric, points=points)


def test_analyze_series_stable_when_flat() -> None:
    analysis = analyze_series(_series([0.10, 0.10, 0.10, 0.10]), window=3, max_adverse_pp=1.0)
    assert analysis.status == "stable"
    assert analysis.gate_fail is False
    assert analysis.consecutive_adverse == 0


def test_analyze_series_degrading_after_window_adverse_deltas_fails_gate() -> None:
    analysis = analyze_series(_series([0.10, 0.11, 0.12, 0.13]), window=3, max_adverse_pp=1.0)
    assert analysis.status == "degrading"
    assert analysis.consecutive_adverse == 3
    assert analysis.gate_fail is True  # +3.0pp over the window > 1.0pp


def test_analyze_series_degrading_status_but_gate_respects_threshold() -> None:
    analysis = analyze_series(_series([0.10, 0.102, 0.104, 0.106]), window=3, max_adverse_pp=1.0)
    assert analysis.status == "degrading"
    assert analysis.gate_fail is False  # +0.6pp total <= 1.0pp


def test_analyze_series_insufficient_data_below_window() -> None:
    analysis = analyze_series(_series([0.10, 0.50]), window=3, max_adverse_pp=1.0)
    assert analysis.status == "insufficient-data"
    assert analysis.gate_fail is False


def test_analyze_higher_is_better_metric_counts_decline_as_adverse() -> None:
    series = _series([30.0, 25.0, 20.0, 15.0], metric="pages_per_minute")
    analysis = analyze_series(series, window=3, max_adverse_pp=1.0)
    assert series.higher_is_better is True
    assert analysis.status == "degrading"
    assert analysis.gate_fail is True


def test_analyze_none_points_are_skipped_never_adverse() -> None:
    analysis = analyze_series(_series([0.10, None, 0.10, 0.10]), window=2, max_adverse_pp=1.0)
    assert analysis.status == "stable"
    assert analysis.gate_fail is False
    assert all(delta is not None for delta in analysis.deltas)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_render_trend_markdown_has_sections_table_and_verdict() -> None:
    series = _series([0.10, 0.12, 0.14, 0.16])
    analysis = analyze_series(series, window=3, max_adverse_pp=1.0)
    text = render_trend_markdown(
        [analysis], history="hist.jsonl", run_count=4, window=3, max_adverse_pp=1.0
    )

    assert text.splitlines()[0] == "# Benchmark Trend"
    assert "hist.jsonl" in text and "- runs: 4" in text
    assert "## engine=mineru scope=aggregate metric=mean_cer (lower is better)" in text
    assert "| 1 | 2026-11-01T00:00:00Z | abc123 | 0.1000 | — |" in text
    assert "| 2 | 2026-11-02T00:00:00Z | abc123 | 0.1200 | +0.0200 |" in text
    assert "verdict: degrading" in text


def test_render_trend_markdown_renders_na_for_none_values() -> None:
    series = _series([0.10, None, 0.10, 0.10], metric="mean_wer")
    analysis = analyze_series(series, window=2, max_adverse_pp=1.0)
    text = render_trend_markdown([analysis], history="h", run_count=4, window=2, max_adverse_pp=1.0)
    assert "| N/A | — |" in text  # None value renders N/A with an em-dash delta
    assert "verdict: stable" in text


def test_trend_json_is_schema_v1() -> None:
    series = _series([0.10, 0.12, 0.14, 0.16])
    analysis = analyze_series(series, window=3, max_adverse_pp=1.0)
    payload = json.loads(
        trend_json([analysis], history="h.jsonl", run_count=4, window=3, max_adverse_pp=1.0)
    )
    assert payload["schema_version"] == 1
    assert payload["kind"] == "benchmark-trend"
    assert payload["run_count"] == 4
    assert payload["series"][0]["engine"] == "mineru"
    assert payload["series"][0]["status"] == "degrading"
    assert payload["series"][0]["gate_fail"] is True
    assert payload["series"][0]["points"][0] == {
        "generated_at": "2026-11-01T00:00:00Z",
        "manifest_sha256": "abc123",
        "value": 0.10,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str], **kwargs) -> int:
    from scribeforge.cli import run

    return run(argv, stdout=io.StringIO(), **kwargs)


def test_cli_benchmark_history_appends_valid_schema_v1_lines(tmp_path: Path) -> None:
    from scribeforge.benchmark.manifest import load_manifest
    from scribeforge.benchmark.multilingual_synthetic import (
        build_multilingual_corpus,
        oracle_for_manifest,
    )

    manifest_path = build_multilingual_corpus(tmp_path / "corpus")
    manifest = load_manifest(manifest_path)
    root = manifest_path.parent
    history = tmp_path / "hist.jsonl"

    def factory(runtime_root, backends):
        return (oracle_for_manifest(manifest, root),)

    for run_index in range(2):
        code = _run_cli(
            [
                "benchmark",
                "--manifest",
                str(manifest_path),
                "--out",
                str(tmp_path / f"r{run_index}.md"),
                "--history",
                str(history),
            ],
            benchmark_engine_factory=factory,
        )
        assert code == 0

    lines = history.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        payload = json.loads(line)
        assert payload["schema_version"] == 1
        assert payload["variants"][0]["engine"] == "oracle"


def test_cli_trend_exit_codes(tmp_path: Path) -> None:
    history = _history_file(
        tmp_path,
        _run_payload(_case(cer=0.10), generated_at="2026-11-01T00:00:00Z"),
        _run_payload(_case(cer=0.12), generated_at="2026-11-02T00:00:00Z"),
        _run_payload(_case(cer=0.14), generated_at="2026-11-03T00:00:00Z"),
        _run_payload(_case(cer=0.16), generated_at="2026-11-04T00:00:00Z"),
    )

    # flat history -> exit 0
    flat = _history_file(
        tmp_path,
        _run_payload(_case(cer=0.10), generated_at="2026-11-01T00:00:00Z"),
        _run_payload(_case(cer=0.10), generated_at="2026-11-02T00:00:00Z"),
        _run_payload(_case(cer=0.10), generated_at="2026-11-03T00:00:00Z"),
        _run_payload(_case(cer=0.10), generated_at="2026-11-04T00:00:00Z"),
        name="flat.jsonl",
    )
    assert _run_cli(["benchmark-trend", str(flat)]) == 0

    # degrading history -> exit 0 without gate, 3 with --fail-on-trend
    assert _run_cli(["benchmark-trend", str(history), "--metric", "mean_cer"]) == 0
    assert (
        _run_cli(
            [
                "benchmark-trend",
                str(history),
                "--metric",
                "mean_cer",
                "--fail-on-trend",
                "--window",
                "3",
            ]
        )
        == 3
    )

    # missing history file is not an error (empty history)
    assert _run_cli(["benchmark-trend", str(tmp_path / "empty.jsonl")]) == 0


def test_cli_trend_json_output_written_to_out(tmp_path: Path) -> None:
    history = _history_file(
        tmp_path,
        _run_payload(_case(cer=0.10), generated_at="2026-11-01T00:00:00Z"),
        _run_payload(_case(cer=0.30), generated_at="2026-11-02T00:00:00Z"),
    )
    out = tmp_path / "trend.json"
    code = _run_cli(
        [
            "benchmark-trend",
            str(history),
            "--metric",
            "mean_cer",
            "--window",
            "1",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["kind"] == "benchmark-trend"
    assert payload["series"][0]["metric"] == "mean_cer"


def test_cli_trend_end_to_end_detects_vertical_degradation(tmp_path: Path) -> None:
    from scribeforge.benchmark.manifest import load_manifest
    from scribeforge.benchmark.multilingual_synthetic import (
        build_multilingual_corpus,
        oracle_for_manifest,
        scripted_engine_for_manifest,
    )

    manifest_path = build_multilingual_corpus(tmp_path / "corpus")
    manifest = load_manifest(manifest_path)
    root = manifest_path.parent
    history = tmp_path / "hist.jsonl"

    def factory_for(engine):
        def factory(runtime_root, backends):
            return (engine,)

        return factory

    for index, engine in enumerate(
        [
            oracle_for_manifest(manifest, root),
            scripted_engine_for_manifest(manifest, root, name="oracle", swap_vertical_columns=True),
        ]
    ):
        code = _run_cli(
            [
                "benchmark",
                "--manifest",
                str(manifest_path),
                "--out",
                str(tmp_path / f"r{index}.md"),
                "--history",
                str(history),
            ],
            benchmark_engine_factory=factory_for(engine),
        )
        assert code == 0

    out_md = tmp_path / "trend.md"
    code = _run_cli(
        [
            "benchmark-trend",
            str(history),
            "--engine",
            "oracle",
            "--metric",
            "reading_order_error_rate",
            "--scope",
            "vertical_reading_order",
            "--window",
            "1",
            "--fail-on-trend",
            "--out",
            str(out_md),
        ]
    )
    assert code == 3
    text = out_md.read_text(encoding="utf-8")
    assert "vertical_reading_order" in text
    assert "degrading" in text
