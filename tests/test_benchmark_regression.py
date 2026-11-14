"""Regression comparison between two schema-v1 benchmark JSON reports (#62/#68/#90).

The benchmark harness can now answer "did this run get worse than the
baseline?" from two ``--report-json`` files, with a threshold gate for CI.
N/A semantics are preserved: a metric that is ``None`` on either side never
turns into 0 — it is reported as ``new`` / ``unavailable`` / ``missing`` /
``na`` with a null delta instead.
"""

from __future__ import annotations

import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from scribeforge.benchmark.regression import (
    RegressionError,
    RegressionThresholds,
    compare_payloads,
    load_report_json,
    regression_json,
    render_regression_markdown,
)
from scribeforge.benchmark.report import render_report_json
from scribeforge.benchmark.runner import (
    BenchmarkReport,
    CaseScore,
    aggregate_case_scores,
)


def _case(
    case_id: str = "case-1",
    *,
    language: str = "en",
    direction: str | None = None,
    cer: float = 0.10,
    wer: float | None = 0.20,
    punctuation_accuracy: float = 0.90,
    reading_order_error_rate: float = 0.0,
) -> CaseScore:
    return CaseScore(
        case_id=case_id,
        language=language,
        tags=("body",),
        direction=direction,
        cer=cer,
        wer=wer,
        punctuation_accuracy=punctuation_accuracy,
        missing_lines=0,
        extra_lines=0,
        reading_order_errors=int(reading_order_error_rate * 10),
        reading_order_error_rate=reading_order_error_rate,
        elapsed_seconds=2.0,
    )


def _report(
    engine: str,
    cases: tuple[CaseScore, ...],
    *,
    elapsed: float = 2.0,
    pages_per_minute: float = 30.0,
) -> BenchmarkReport:
    aggregate = replace(
        aggregate_case_scores(cases),
        elapsed_seconds=elapsed,
        pages_per_minute=pages_per_minute,
    )
    return BenchmarkReport(engine=engine, cases=cases, aggregate=aggregate)


def _payload(*reports: BenchmarkReport, generated_at: str = "2026-11-05T00:00:00Z") -> dict:
    return json.loads(
        render_report_json(
            list(reports),
            corpus="corpus/manifest.json",
            manifest_sha256="abc123",
            generated_at=generated_at,
        )
    )


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# load_report_json validation
# ---------------------------------------------------------------------------


def test_load_report_json_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RegressionError, match="not found"):
        load_report_json(tmp_path / "nope.json")


def test_load_report_json_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RegressionError, match="not valid JSON"):
        load_report_json(path)


def test_load_report_json_rejects_wrong_schema_version(tmp_path: Path) -> None:
    payload = _payload(_report("mineru", (_case(),)))
    payload["schema_version"] = 99
    with pytest.raises(RegressionError, match="schema_version"):
        load_report_json(_write(tmp_path, "v99.json", payload))


def test_load_report_json_rejects_missing_variants(tmp_path: Path) -> None:
    payload = _payload(_report("mineru", (_case(),)))
    del payload["variants"]
    with pytest.raises(RegressionError, match="variants"):
        load_report_json(_write(tmp_path, "novariants.json", payload))


def test_load_report_json_rejects_variant_without_engine(tmp_path: Path) -> None:
    payload = _payload(_report("mineru", (_case(),)))
    del payload["variants"][0]["engine"]
    with pytest.raises(RegressionError, match="engine"):
        load_report_json(_write(tmp_path, "noengine.json", payload))


def test_load_report_json_accepts_schema_v1(tmp_path: Path) -> None:
    payload = _payload(_report("mineru", (_case(),)))
    loaded = load_report_json(_write(tmp_path, "ok.json", payload))
    assert loaded["schema_version"] == 1
    assert loaded["variants"][0]["engine"] == "mineru"


# ---------------------------------------------------------------------------
# aggregate comparison semantics
# ---------------------------------------------------------------------------


def test_compare_detects_regressed_improved_and_unchanged() -> None:
    baseline = _payload(_report("mineru", (_case(cer=0.10, wer=0.20, punctuation_accuracy=0.90),)))
    current = _payload(
        _report(
            "mineru",
            (_case(cer=0.12, wer=0.15, punctuation_accuracy=0.90),),
            pages_per_minute=20.0,
        )
    )

    report = compare_payloads(baseline, current)
    deltas = {(d.scope, d.metric): d for d in report.comparisons[0].deltas}

    cer = deltas[("aggregate", "mean_cer")]
    assert cer.status == "regressed"
    assert cer.baseline == pytest.approx(0.10)
    assert cer.current == pytest.approx(0.12)
    assert cer.delta == pytest.approx(0.02)

    wer = deltas[("aggregate", "mean_wer")]
    assert wer.status == "improved"
    assert wer.delta == pytest.approx(-0.05)

    punct = deltas[("aggregate", "mean_punctuation_accuracy")]
    assert punct.status == "unchanged"
    assert punct.delta == 0.0

    ppm = deltas[("aggregate", "pages_per_minute")]
    assert ppm.status == "regressed"  # higher is better
    assert ppm.higher_is_better is True


def test_wer_none_stays_none_and_never_becomes_zero() -> None:
    baseline = _payload(_report("mineru", (_case(language="zh", wer=None),)))
    current = _payload(_report("mineru", (_case(language="zh", wer=None),)))

    report = compare_payloads(baseline, current)
    deltas = {(d.scope, d.metric): d for d in report.comparisons[0].deltas}

    for metric in ("mean_wer", "micro_wer"):
        delta = deltas[("aggregate", metric)]
        assert delta.status == "na"
        assert delta.baseline is None and delta.current is None and delta.delta is None

    case_wer = deltas[("case:case-1", "wer")]
    assert case_wer.status == "na"
    assert case_wer.delta is None

    # the JSON rendering must serialize null, not 0
    payload = json.loads(regression_json(report))
    engine = payload["engines"][0]
    wer_delta = next(d for d in engine["deltas"] if d["metric"] == "mean_wer")
    assert wer_delta["baseline"] is None
    assert wer_delta["current"] is None
    assert wer_delta["delta"] is None
    assert wer_delta["status"] == "na"


def test_unavailable_and_new_values_are_not_regressions() -> None:
    baseline = _payload(_report("mineru", (_case(wer=0.40),)))
    current = _payload(_report("mineru", (_case(wer=None),)))
    report = compare_payloads(baseline, current)
    unavailable = next(
        d for d in report.comparisons[0].deltas if d.scope == "aggregate" and d.metric == "mean_wer"
    )
    assert unavailable.status == "unavailable"
    assert unavailable.delta is None
    assert unavailable.is_regression is False

    flipped = compare_payloads(current, baseline)
    new = next(
        d
        for d in flipped.comparisons[0].deltas
        if d.scope == "aggregate" and d.metric == "mean_wer"
    )
    assert new.status == "new"
    assert new.is_regression is False


# ---------------------------------------------------------------------------
# strata: language / direction / vertical / per-case / engine presence
# ---------------------------------------------------------------------------


def test_per_language_strata_are_compared_by_language() -> None:
    baseline = _payload(
        _report(
            "mineru",
            (_case("zh-1", language="zh", cer=0.10), _case("en-1", language="en", cer=0.20)),
        )
    )
    current = _payload(
        _report(
            "mineru",
            (_case("zh-1", language="zh", cer=0.15), _case("en-1", language="en", cer=0.10)),
        )
    )

    report = compare_payloads(baseline, current)
    deltas = {(d.scope, d.metric): d for d in report.comparisons[0].deltas}
    assert deltas[("language:zh", "mean_cer")].status == "regressed"
    assert deltas[("language:en", "mean_cer")].status == "improved"


def test_language_only_in_current_is_marked_new() -> None:
    baseline = _payload(_report("mineru", (_case("en-1", language="en"),)))
    current = _payload(
        _report(
            "mineru",
            (_case("en-1", language="en"), _case("ja-1", language="ja", direction="vertical-rl")),
        )
    )

    report = compare_payloads(baseline, current)
    ja = [d for d in report.comparisons[0].deltas if d.scope == "language:ja"]
    assert ja and all(d.status == "new" and d.baseline is None for d in ja)


def test_direction_and_vertical_reading_order_are_compared() -> None:
    baseline = _payload(
        _report(
            "mineru",
            (_case("ja-1", language="ja", direction="vertical-rl", reading_order_error_rate=0.0),),
        )
    )
    current = _payload(
        _report(
            "mineru",
            (_case("ja-1", language="ja", direction="vertical-rl", reading_order_error_rate=0.5),),
        )
    )

    report = compare_payloads(baseline, current)
    deltas = {(d.scope, d.metric): d for d in report.comparisons[0].deltas}
    assert deltas[("direction:vertical-rl", "reading_order_error_rate")].status == "regressed"
    vertical = deltas[("vertical_reading_order", "reading_order_error_rate")]
    assert vertical.status == "regressed"
    assert vertical.delta == pytest.approx(0.5)


def test_per_case_deltas_match_by_case_id_and_missing_cases_are_marked() -> None:
    baseline = _payload(_report("mineru", (_case("a"), _case("b", cer=0.30))))
    current = _payload(_report("mineru", (_case("a", cer=0.11),)))

    report = compare_payloads(baseline, current)
    deltas = {(d.scope, d.metric): d for d in report.comparisons[0].deltas}
    assert deltas[("case:a", "cer")].status == "regressed"
    missing = deltas[("case:b", "cer")]
    assert missing.status == "missing"
    assert missing.current is None and missing.delta is None


def test_engine_present_on_one_side_only_has_no_deltas() -> None:
    baseline = _payload(_report("mineru", (_case(),)))
    current = _payload(_report("mineru", (_case(),)), _report("surya", (_case(),)))

    report = compare_payloads(baseline, current)
    by_engine = {c.engine: c for c in report.comparisons}
    assert by_engine["mineru"].presence == "both"
    assert by_engine["surya"].presence == "current-only"
    assert by_engine["surya"].deltas == ()


# ---------------------------------------------------------------------------
# threshold gate
# ---------------------------------------------------------------------------


def test_gate_failures_respect_default_thresholds() -> None:
    thresholds = RegressionThresholds()
    baseline = _payload(
        _report(
            "mineru",
            (_case(cer=0.10, wer=0.20, reading_order_error_rate=0.10),),
        )
    )
    current = _payload(
        _report(
            "mineru",
            (_case(cer=0.106, wer=0.215, reading_order_error_rate=0.12),),
        )
    )

    report = compare_payloads(baseline, current)
    failures = report.gate_failures(thresholds)
    families = {(d.scope, d.metric) for d in failures}
    # cer +0.6pp > 0.5pp, wer +1.5pp > 1.0pp, ro +2.0pp > 1.0pp -> all fail
    assert ("aggregate", "mean_cer") in families
    assert ("aggregate", "mean_wer") in families
    assert ("aggregate", "reading_order_error_rate") in families


def test_gate_passes_when_deltas_are_within_thresholds() -> None:
    thresholds = RegressionThresholds()
    baseline = _payload(
        _report("mineru", (_case(cer=0.10, wer=0.20, reading_order_error_rate=0.10),))
    )
    current = _payload(
        _report("mineru", (_case(cer=0.104, wer=0.21, reading_order_error_rate=0.105),))
    )

    report = compare_payloads(baseline, current)
    assert report.gate_failures(thresholds) == ()


def test_gate_ignores_ungated_metrics_even_when_regressed() -> None:
    thresholds = RegressionThresholds()
    baseline = _payload(
        _report("mineru", (_case(punctuation_accuracy=0.95),), pages_per_minute=30.0)
    )
    current = _payload(
        _report("mineru", (_case(punctuation_accuracy=0.80),), pages_per_minute=10.0)
    )

    report = compare_payloads(baseline, current)
    deltas = {(d.scope, d.metric): d for d in report.comparisons[0].deltas}
    assert deltas[("aggregate", "mean_punctuation_accuracy")].status == "regressed"
    assert deltas[("aggregate", "pages_per_minute")].status == "regressed"
    assert report.gate_failures(thresholds) == ()  # no cer/wer/ro regression


def test_custom_thresholds_and_validation() -> None:
    baseline = _payload(_report("mineru", (_case(cer=0.10),)))
    current = _payload(_report("mineru", (_case(cer=0.106),)))

    report = compare_payloads(baseline, current)
    assert report.gate_failures(RegressionThresholds(cer_pp=2.0)) == ()
    failures = report.gate_failures(RegressionThresholds(cer_pp=0.5))
    assert failures  # aggregate mean_cer + per-language + per-case cer all regress
    assert all("cer" in d.metric for d in failures)

    with pytest.raises(ValueError, match="non-negative"):
        RegressionThresholds(cer_pp=-1.0)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def test_render_markdown_has_header_engine_tables_and_gate_verdict() -> None:
    baseline = _payload(
        _report("mineru", (_case(language="zh", wer=None, cer=0.10),)),
        generated_at="2026-11-01T00:00:00Z",
    )
    current = _payload(
        _report("mineru", (_case(language="zh", wer=None, cer=0.20),)),
        generated_at="2026-11-05T00:00:00Z",
    )

    report = compare_payloads(baseline, current)
    thresholds = RegressionThresholds()
    text = render_regression_markdown(report, thresholds)

    lines = text.splitlines()
    assert lines[0] == "# Benchmark Regression Comparison"
    assert any("baseline" in line and "2026-11-01" in line for line in lines)
    assert any("current" in line and "2026-11-05" in line for line in lines)
    assert "## Engine: mineru" in text
    assert "| aggregate | mean_cer | 0.1000 | 0.2000 | +0.1000 | regressed |" in text
    # N/A must render as N/A, never as 0.0000
    assert "| aggregate | mean_wer | N/A | N/A | N/A | na |" in text
    assert "0.0000 | N/A" not in text
    assert "## Gate: FAIL" in text
    assert "mean_cer" in text.split("## Gate failures", 1)[1]


def test_render_markdown_gate_pass_when_no_failures() -> None:
    baseline = _payload(_report("mineru", (_case(cer=0.10),)))
    current = _payload(_report("mineru", (_case(cer=0.10),)))
    text = render_regression_markdown(compare_payloads(baseline, current), RegressionThresholds())
    assert "## Gate: PASS" in text
    assert "## Gate failures" in text  # section exists, lists none


def test_regression_json_is_schema_v1_with_gate_block() -> None:
    baseline = _payload(_report("mineru", (_case(cer=0.10),)))
    current = _payload(_report("mineru", (_case(cer=0.20),)))
    report = compare_payloads(baseline, current)
    payload = json.loads(regression_json(report, RegressionThresholds()))

    assert payload["schema_version"] == 1
    assert payload["kind"] == "benchmark-regression"
    assert payload["baseline"]["manifest_sha256"] == "abc123"
    assert payload["thresholds"] == {"cer_pp": 0.5, "wer_pp": 1.0, "reading_order_pp": 1.0}
    assert payload["gate"]["passed"] is False
    assert payload["gate"]["failures"][0]["metric"] == "mean_cer"
    assert payload["engines"][0]["engine"] == "mineru"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str]):
    from scribeforge.cli import run

    return run(argv, stdout=io.StringIO())


def test_cli_compare_ok_exit_zero_and_writes_markdown(tmp_path: Path) -> None:
    baseline = _write(tmp_path, "base.json", _payload(_report("mineru", (_case(cer=0.10),))))
    current = _write(tmp_path, "cur.json", _payload(_report("mineru", (_case(cer=0.10),))))
    out = tmp_path / "cmp.md"

    code = _run_cli(["benchmark-compare", str(baseline), str(current), "--out", str(out)])

    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Benchmark Regression Comparison")
    assert "## Gate: PASS" in text


def test_cli_compare_fail_on_regression_exit_three(tmp_path: Path) -> None:
    baseline = _write(tmp_path, "base.json", _payload(_report("mineru", (_case(cer=0.10),))))
    current = _write(tmp_path, "cur.json", _payload(_report("mineru", (_case(cer=0.30),))))

    code = _run_cli(["benchmark-compare", str(baseline), str(current), "--fail-on-regression"])
    assert code == 3

    # without the flag the same comparison exits 0
    code = _run_cli(["benchmark-compare", str(baseline), str(current)])
    assert code == 0


def test_cli_compare_bad_input_exits_fatal(tmp_path: Path) -> None:
    baseline = _write(tmp_path, "base.json", _payload(_report("mineru", (_case(),))))
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")

    code = _run_cli(["benchmark-compare", str(baseline), str(bad)])
    assert code == 2


def test_cli_compare_json_output_and_custom_thresholds(tmp_path: Path) -> None:
    baseline = _write(tmp_path, "base.json", _payload(_report("mineru", (_case(cer=0.10),))))
    current = _write(tmp_path, "cur.json", _payload(_report("mineru", (_case(cer=0.11),))))
    out = tmp_path / "cmp.json"

    # +1.0pp cer fails the default 0.5pp gate but passes a 2.0pp custom gate
    code = _run_cli(
        [
            "benchmark-compare",
            str(baseline),
            str(current),
            "--fail-on-regression",
            "--json",
            "--out",
            str(out),
        ]
    )
    assert code == 3
    assert json.loads(out.read_text(encoding="utf-8"))["gate"]["passed"] is False

    code = _run_cli(
        [
            "benchmark-compare",
            str(baseline),
            str(current),
            "--fail-on-regression",
            "--cer-pp",
            "2.0",
        ]
    )
    assert code == 0


def test_cli_end_to_end_on_synthetic_corpus_detects_vertical_regression(tmp_path: Path) -> None:
    from scribeforge.benchmark.manifest import load_manifest
    from scribeforge.benchmark.multilingual_synthetic import (
        build_multilingual_corpus,
        oracle_for_manifest,
        scripted_engine_for_manifest,
    )

    manifest_path = build_multilingual_corpus(tmp_path / "corpus")
    manifest = load_manifest(manifest_path)
    root = manifest_path.parent

    base_json = tmp_path / "base.json"
    cur_json = tmp_path / "cur.json"
    out_md = tmp_path / "cmp.md"

    def factory_for(engine):
        def factory(runtime_root, backends):
            return (engine,)

        return factory

    base_code = _run_cli_with_factory(
        manifest_path, base_json, factory_for(oracle_for_manifest(manifest, root))
    )
    cur_code = _run_cli_with_factory(
        manifest_path,
        cur_json,
        # same engine name as the oracle run so the comparison matches variants
        factory_for(
            scripted_engine_for_manifest(manifest, root, name="oracle", swap_vertical_columns=True)
        ),
    )
    assert base_code == 0 and cur_code == 0

    code = _run_cli(
        [
            "benchmark-compare",
            str(base_json),
            str(cur_json),
            "--fail-on-regression",
            "--out",
            str(out_md),
        ]
    )
    assert code == 3
    text = out_md.read_text(encoding="utf-8")
    assert "vertical_reading_order" in text
    assert "regressed" in text


def _run_cli_with_factory(manifest_path: Path, out_json: Path, factory) -> int:
    from scribeforge.cli import run

    return run(
        [
            "benchmark",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out_json.with_suffix(".md")),
            "--report-json",
            str(out_json),
        ],
        benchmark_engine_factory=factory,
        stdout=io.StringIO(),
    )
