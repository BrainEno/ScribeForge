"""Synthetic multilingual benchmark corpus (issues #62/#68).

Real en/ja scans are still pending, so the *harness* is verified on a
deterministic synthetic multilingual corpus instead: zh/en/ja cases including
an English two-column page and Japanese vertical pages, scored by the oracle
engine (which replays the answer key — a pipeline-mechanics check, never an
OCR accuracy claim). This proves the per-language / per-direction / vertical
reading-order report paths execute on all three languages in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

from scribeforge.benchmark.manifest import load_manifest
from scribeforge.benchmark.multilingual_synthetic import (
    build_multilingual_corpus,
    oracle_for_manifest,
    scripted_engine_for_manifest,
)
from scribeforge.benchmark.quality_gate import QualityGateSettings, check_corpus_quality
from scribeforge.benchmark.report import render_report, render_report_json
from scribeforge.benchmark.runner import run_benchmark


def _corpus(tmp_path: Path) -> Path:
    return build_multilingual_corpus(tmp_path / "corpus")


def test_corpus_spans_zh_en_ja_with_layout_cases(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)

    languages = {case.language for case in manifest.cases}
    assert languages == {"zh", "en", "ja"}
    en_cases = [c for c in manifest.cases if c.language == "en"]
    assert any("two-column" in c.tags for c in en_cases)
    ja_cases = [c for c in manifest.cases if c.language == "ja"]
    assert ja_cases and all(c.direction == "vertical-rl" for c in ja_cases)
    assert manifest.license == "CC0-1.0"
    assert all(c.source for c in manifest.cases)


def test_oracle_benchmark_report_has_all_multilingual_sections(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    engine = oracle_for_manifest(manifest, manifest_path.parent)

    report = run_benchmark(manifest, manifest_path.parent, engine)

    assert report.aggregate.mean_cer == 0.0  # oracle replays the answer key
    text = render_report(
        [report], corpus=str(manifest_path), manifest_sha256="h", generated_at="now"
    )
    assert "## Results by language" in text
    for language in ("en", "ja", "zh"):
        assert f"| oracle | {language} |" in text
    assert "## Results by writing direction" in text
    assert "| oracle | vertical-rl |" in text
    assert "## Vertical text reading order" in text
    assert "| oracle |" in text.split("## Vertical text reading order", 1)[1]


def test_oracle_benchmark_json_has_language_and_vertical_strata(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    report = run_benchmark(
        manifest, manifest_path.parent, oracle_for_manifest(manifest, manifest_path.parent)
    )

    payload = json.loads(
        render_report_json([report], corpus="c", manifest_sha256="h", generated_at="now")
    )
    variant = payload["variants"][0]
    assert {row["language"] for row in variant["by_language"]} == {"en", "ja", "zh"}
    directions = {row["direction"] for row in variant["by_direction"]}
    assert "vertical-rl" in directions
    assert variant["vertical_reading_order"][0]["cases"] >= 1
    assert variant["vertical_reading_order"][0]["reading_order_error_rate"] == 0.0


def test_quality_gate_accepts_languages_but_flags_small_synthetic_corpus(tmp_path: Path) -> None:
    """Languages are covered; only the size rule fires (it is a harness fixture)."""
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)

    issues = check_corpus_quality(
        manifest, settings=QualityGateSettings(check_images=True), root=manifest_path.parent
    )
    codes = {issue.code for issue in issues}
    assert "language-missing" not in codes
    assert "vertical-sample-missing" not in codes
    assert "two-column-sample-missing" not in codes
    assert "corpus-too-small" in codes  # 6 synthetic pages < 30: expected
    assert not any(
        issue.severity == "error" and issue.code.startswith("license") for issue in issues
    )


def test_cli_check_only_reports_all_languages_on_synthetic_corpus(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    manifest_path = _corpus(tmp_path)
    out = tmp_path / "gate.json"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
            "--check-only",
            "--quality-json",
        ],
        stdout=io.StringIO(),
    )

    assert code == 2  # corpus-too-small error, but languages are covered
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert set(payload["languages"]) == {"en", "ja", "zh"}
    codes = {issue["code"] for issue in payload["issues"]}
    assert "language-missing" not in codes
    assert "corpus-too-small" in codes


# ---------------------------------------------------------------------------
# scripted engines: prove the vertical reading-order metric actually fires
# ---------------------------------------------------------------------------


class _ColumnSwappingOracle(
    OracleEngine := __import__(
        "scribeforge.benchmark.oracle", fromlist=["OracleEngine"]
    ).OracleEngine
):
    """Oracle that reads vertical pages' columns left-to-right (wrong order)."""

    def analyze_page(self, image_path: str, page_index: int):  # type: ignore[no-untyped-def]
        from dataclasses import replace

        result = super().analyze_page(image_path, page_index)
        key = __import__("pathlib").Path(image_path).resolve()
        texts, direction = self._texts[key]
        if direction.value.startswith("vertical"):
            # Swap the first two columns: the text is right, the order is wrong.
            swapped = (texts[1], texts[0], *texts[2:])
            lines = tuple(replace(line, text=text) for line, text in zip(result.lines, swapped))
            return replace(result, lines=lines)
        return result


def test_vertical_column_swap_produces_reading_order_errors(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    engine = _ColumnSwappingOracle("swapper")
    for case in manifest.cases:
        from scribeforge.benchmark.multilingual_synthetic import _DIRECTION_TO_PAGE

        engine.register(
            manifest_path.parent / case.image,
            (manifest_path.parent / case.ground_truth).read_text(encoding="utf-8"),
            direction=_DIRECTION_TO_PAGE[case.direction or "horizontal-lr"],
        )

    report = run_benchmark(manifest, manifest_path.parent, engine)

    ja_cases = [c for c in report.cases if c.language == "ja"]
    assert ja_cases and all(c.reading_order_errors > 0 for c in ja_cases)
    assert all(c.reading_order_error_rate > 0 for c in ja_cases)
    zh_en = [c for c in report.cases if c.language != "ja"]
    assert all(c.reading_order_errors == 0 for c in zh_en)

    text = render_report([report], corpus="c", manifest_sha256="h", generated_at="now")
    vertical_section = text.split("## Vertical text reading order", 1)[1].split("## ", 1)[0]
    swapper_row = next(
        line for line in vertical_section.splitlines() if line.startswith("| swapper |")
    )
    cells = [cell.strip() for cell in swapper_row.split("|")]
    # | swapper | cases | mean CER | reading-order errors | RO err rate |
    assert cells[2] == "2"  # both ja pages counted
    assert int(cells[4]) > 0  # inversions detected
    assert float(cells[5]) > 0.0  # rate is non-zero


def test_json_vertical_strata_carry_nonzero_rate(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    engine = _ColumnSwappingOracle("swapper")
    from scribeforge.benchmark.multilingual_synthetic import _DIRECTION_TO_PAGE

    for case in manifest.cases:
        engine.register(
            manifest_path.parent / case.image,
            (manifest_path.parent / case.ground_truth).read_text(encoding="utf-8"),
            direction=_DIRECTION_TO_PAGE[case.direction or "horizontal-lr"],
        )
    report = run_benchmark(manifest, manifest_path.parent, engine)

    payload = json.loads(
        render_report_json([report], corpus="c", manifest_sha256="h", generated_at="now")
    )
    vertical = payload["variants"][0]["vertical_reading_order"][0]
    assert vertical["cases"] == 2
    assert vertical["reading_order_errors"] > 0
    assert vertical["reading_order_error_rate"] > 0
    by_language = {row["language"]: row for row in payload["variants"][0]["by_language"]}
    assert by_language["ja"]["reading_order_error_rate"] > 0
    assert by_language["en"]["reading_order_error_rate"] == 0.0


def test_annotation_status_on_synthetic_corpus(tmp_path: Path) -> None:
    from scribeforge.benchmark.annotation import annotation_status, annotation_status_json

    manifest_path = _corpus(tmp_path)
    status = annotation_status(manifest_path.parent)

    assert status.annotation_complete is True  # all synthetic truths are filled
    assert status.languages == {"en": 2, "ja": 2, "zh": 2}
    assert status.directions == {"horizontal-lr": 4, "vertical-rl": 2}
    payload = json.loads(annotation_status_json(status))
    assert payload["annotation_complete"] is True


def test_synthetic_corpus_passes_image_existence_check(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)

    issues = check_corpus_quality(
        manifest,
        settings=QualityGateSettings(check_images=True),
        root=manifest_path.parent,
    )
    codes = {issue.code for issue in issues}
    assert "image-missing" not in codes
    assert "image-path-unsafe" not in codes


def test_report_per_case_rows_carry_language_and_direction_metadata(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    report = run_benchmark(
        manifest, manifest_path.parent, oracle_for_manifest(manifest, manifest_path.parent)
    )

    text = render_report([report], corpus="c", manifest_sha256="h", generated_at="now")
    per_case = text.split("## Per-case results", 1)[1]
    assert "| syn-ja-001 | ja | synthetic,vertical |" in per_case
    assert "| syn-en-002 | en | synthetic,two-column |" in per_case
    assert "| syn-zh-001 | zh | synthetic,body |" in per_case


def test_build_is_deterministic(tmp_path: Path) -> None:
    first = build_multilingual_corpus(tmp_path / "a")
    second = build_multilingual_corpus(tmp_path / "b")

    assert first.read_bytes() == second.read_bytes()
    for case_image in sorted((tmp_path / "a" / "images").iterdir()):
        twin = tmp_path / "b" / "images" / case_image.name
        assert case_image.read_bytes() == twin.read_bytes()


def test_cli_annotation_status_on_synthetic_corpus(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    manifest_path = _corpus(tmp_path)
    out = tmp_path / "status.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
            "--annotation-status",
        ],
        stdout=io.StringIO(),
    )

    assert code == 0  # synthetic corpus is fully annotated
    text = out.read_text(encoding="utf-8")
    assert "| en | 2 |" in text and "| ja | 2 |" in text and "| zh | 2 |" in text
    assert "| vertical-rl | 2 |" in text


def test_synthetic_manifest_stays_schema_v1_valid(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)

    assert manifest.schema_version == 1
    assert manifest.license == "CC0-1.0"
    assert manifest.ground_truth_review_status == "single-pass"
    assert manifest.manifest_sha256 is not None


# ---------------------------------------------------------------------------
# language-aware metric semantics on the synthetic corpus
# ---------------------------------------------------------------------------


def test_wer_is_na_for_zh_and_ja_but_applicable_for_en(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    report = run_benchmark(
        manifest, manifest_path.parent, oracle_for_manifest(manifest, manifest_path.parent)
    )

    for case in report.cases:
        if case.language in ("zh", "ja"):
            assert case.wer is None
            assert case.word_tokenizer == "not-applicable"
        else:
            assert case.wer == 0.0
            assert case.word_tokenizer == "whitespace-v1"

    text = render_report([report], corpus="c", manifest_sha256="h", generated_at="now")
    language_section = text.split("## Results by language", 1)[1].split("## ", 1)[0]
    assert "N/A (0 / 2)" in language_section  # ja row: WER N/A with denominator shown
    assert "0.0000" in language_section  # en row: applicable WER


def test_two_variants_appear_in_language_strata(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    root = manifest_path.parent
    perfect = run_benchmark(manifest, root, oracle_for_manifest(manifest, root))
    swapped = run_benchmark(
        manifest,
        root,
        scripted_engine_for_manifest(manifest, root, name="swapper", swap_vertical_columns=True),
    )

    payload = json.loads(
        render_report_json([perfect, swapped], corpus="c", manifest_sha256="h", generated_at="now")
    )
    assert [v["engine"] for v in payload["variants"]] == ["oracle", "swapper"]
    for variant in payload["variants"]:
        assert {row["language"] for row in variant["by_language"]} == {"en", "ja", "zh"}
    swapper_ja = next(
        row for row in payload["variants"][1]["by_language"] if row["language"] == "ja"
    )
    oracle_ja = next(
        row for row in payload["variants"][0]["by_language"] if row["language"] == "ja"
    )
    assert swapper_ja["reading_order_error_rate"] > oracle_ja["reading_order_error_rate"] == 0.0


def test_punctuation_accuracy_is_perfect_under_oracle(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    report = run_benchmark(
        manifest, manifest_path.parent, oracle_for_manifest(manifest, manifest_path.parent)
    )

    assert all(case.punctuation_accuracy == 1.0 for case in report.cases)
    assert report.aggregate.mean_punctuation_accuracy == 1.0


def test_horizontal_cases_have_zero_reading_order_rate(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    report = run_benchmark(
        manifest,
        manifest_path.parent,
        scripted_engine_for_manifest(
            manifest, manifest_path.parent, name="swapper", swap_vertical_columns=True
        ),
    )

    horizontal = [c for c in report.cases if (c.direction or "").startswith("horizontal")]
    assert horizontal
    assert all(c.reading_order_errors == 0 for c in horizontal)
    assert all(c.reading_order_error_rate == 0.0 for c in horizontal)


def test_gate_has_no_undeclared_language_warning_on_synthetic_corpus(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    issues = check_corpus_quality(
        manifest, settings=QualityGateSettings(), root=manifest_path.parent
    )

    assert "undeclared-language" not in {i.code for i in issues}
    assert "blank-language" not in {i.code for i in issues}
    assert "duplicate-image" not in {i.code for i in issues}


def test_report_metric_semantics_mention_na_wer_for_cjk(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    report = run_benchmark(
        manifest, manifest_path.parent, oracle_for_manifest(manifest, manifest_path.parent)
    )
    text = render_report([report], corpus="c", manifest_sha256="h", generated_at="now")

    assert "not-applicable" in text  # metric semantics block explains CJK WER N/A


def test_cli_annotation_status_json_on_synthetic_corpus(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run

    manifest_path = _corpus(tmp_path)
    out = tmp_path / "status.json"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
            "--annotation-status",
            "--quality-json",
        ],
        stdout=io.StringIO(),
    )

    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["annotation_complete"] is True
    assert payload["languages"] == {"en": 2, "ja": 2, "zh": 2}
    assert payload["directions"] == {"horizontal-lr": 4, "vertical-rl": 2}


def test_vertical_section_absent_for_all_horizontal_corpus(tmp_path: Path) -> None:
    """A corpus without vertical cases must not render the vertical section."""
    from scribeforge.benchmark.runner import BenchmarkReport, CaseScore, aggregate_case_scores

    case = CaseScore(
        case_id="h1",
        language="en",
        tags=("body",),
        cer=0.1,
        wer=0.2,
        punctuation_accuracy=0.9,
        missing_lines=0,
        extra_lines=0,
        reading_order_errors=0,
        reading_order_error_rate=0.0,
        elapsed_seconds=1.0,
        direction="horizontal-lr",
    )
    report = BenchmarkReport(engine="e", cases=(case,), aggregate=aggregate_case_scores((case,)))
    text = render_report([report], corpus="c", manifest_sha256="h", generated_at="now")

    assert "## Vertical text reading order" not in text


def test_every_synthetic_case_declares_synthetic_source(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)

    assert all(c.source and "synthetic" in c.source.lower() for c in manifest.cases)


def test_manifest_sha256_is_stable_across_builds(tmp_path: Path) -> None:
    first = load_manifest(build_multilingual_corpus(tmp_path / "a"))
    second = load_manifest(build_multilingual_corpus(tmp_path / "b"))

    assert first.manifest_sha256 == second.manifest_sha256


def test_oracle_engine_reports_ground_truth_version(tmp_path: Path) -> None:
    manifest_path = _corpus(tmp_path)
    manifest = load_manifest(manifest_path)
    engine = oracle_for_manifest(manifest, manifest_path.parent)

    assert engine.name == "oracle"
    result = engine.analyze_page(str(manifest_path.parent / manifest.cases[0].image), 0)
    assert result.engine_version == "ground-truth"
