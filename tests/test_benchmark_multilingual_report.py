"""Multilingual benchmark reporting slice for issue #68.

The report must (a) always carry a per-language CER table, (b) report a
reading-order error *rate* (inversions per reference line) next to the raw
count, and (c) give vertical-writing pages their own reading-order section,
because reading order is the core vertical-layout metric. Real en/ja scans
are still pending (the quality gate keeps flagging them); this slice makes
the harness and the report honest about that gap.
"""

from __future__ import annotations

from pathlib import Path

from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest, load_manifest
from scribeforge.benchmark.metrics import reading_order_error_rate, reading_order_errors
from scribeforge.benchmark.report import render_report
from scribeforge.benchmark.runner import (
    AggregateScore,
    BenchmarkReport,
    CaseScore,
    aggregate_case_scores,
    run_benchmark,
)
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult


def _case(
    case_id: str = "case-1",
    *,
    language: str = "en",
    cer: float = 0.25,
    tags: tuple[str, ...] = ("body",),
    direction: str | None = None,
    reading_order_errors: int = 2,
    reading_order_error_rate: float = 0.2,
    wer: float | None = 0.5,
) -> CaseScore:
    return CaseScore(
        case_id=case_id,
        language=language,
        tags=tags,
        cer=cer,
        wer=wer,
        punctuation_accuracy=0.75,
        missing_lines=0,
        extra_lines=1,
        reading_order_errors=reading_order_errors,
        reading_order_error_rate=reading_order_error_rate,
        elapsed_seconds=2.0,
        direction=direction,
    )


def _report(cases: tuple[CaseScore, ...], engine: str = "ocr") -> BenchmarkReport:
    return BenchmarkReport(
        engine=engine,
        cases=cases,
        aggregate=aggregate_case_scores(cases),
    )


# ---------------------------------------------------------------------------
# metrics.reading_order_error_rate
# ---------------------------------------------------------------------------


def test_reading_order_error_rate_is_inversions_per_reference_line() -> None:
    reference = ["line-a", "line-b", "line-c", "line-d"]
    hypothesis = ["line-c", "line-a", "line-b", "line-d"]  # 2 inversions

    assert reading_order_errors(reference, hypothesis) == 2
    assert reading_order_error_rate(reference, hypothesis) == 0.5


def test_reading_order_error_rate_is_zero_without_inversions() -> None:
    reference = ["a", "b"]
    assert reading_order_error_rate(reference, list(reference)) == 0.0


def test_reading_order_error_rate_empty_reference_is_zero_not_na() -> None:
    # The zero-reference convention from error_rate_from_counts applies:
    # no reference lines and no inversions is a defined 0.0, not an inference.
    assert reading_order_error_rate([], ["anything"]) == 0.0
    assert reading_order_error_rate([], []) == 0.0


def test_reading_order_error_rate_unmatched_lines_do_not_count() -> None:
    # Omissions/insertions are measured by the line metrics; only inversions
    # among exactly matched lines feed the reading-order rate.
    reference = ["a", "b", "c"]
    hypothesis = ["c", "b", "a", "extra"]
    assert reading_order_errors(reference, hypothesis) == 3
    assert reading_order_error_rate(reference, hypothesis) == 1.0


# ---------------------------------------------------------------------------
# runner: CaseScore / AggregateScore carry the rate
# ---------------------------------------------------------------------------


class _SwapEngine:
    name = "swapper"

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        return PageOCRResult(
            page_index=page_index,
            engine=self.name,
            engine_version="1",
            lines=(
                OCRLine("second line", BoundingBox(0.0, 0.5, 0.5, 0.4)),
                OCRLine("first line", BoundingBox(0.0, 0.0, 0.5, 0.4)),
            ),
        )


def test_run_benchmark_populates_reading_order_error_rate(tmp_path: Path) -> None:
    (tmp_path / "page.png").write_bytes(b"fixture")
    (tmp_path / "truth.txt").write_text("first line\nsecond line", encoding="utf-8")
    manifest = BenchmarkManifest(
        1,
        (BenchmarkCase("case-1", "page.png", "truth.txt", "ja", ("vertical",), "vertical-rl"),),
    )

    report = run_benchmark(manifest, tmp_path, _SwapEngine())

    case = report.cases[0]
    assert case.reading_order_errors == 1
    assert case.reading_order_error_rate == 0.5
    assert report.aggregate.reading_order_errors == 1
    assert report.aggregate.reading_order_error_rate == 0.5


def test_aggregate_reading_order_error_rate_is_the_macro_mean() -> None:
    aggregate = aggregate_case_scores(
        (
            _case("a", reading_order_error_rate=0.0, reading_order_errors=0),
            _case("b", reading_order_error_rate=0.4, reading_order_errors=4),
        )
    )

    assert aggregate.reading_order_error_rate == 0.2
    assert aggregate.reading_order_errors == 4


def test_aggregate_reading_order_error_rate_of_no_cases_is_zero() -> None:
    assert aggregate_case_scores(()).reading_order_error_rate == 0.0


# ---------------------------------------------------------------------------
# report: mandatory per-language table with reading-order columns
# ---------------------------------------------------------------------------


def test_language_table_is_mandatory_even_for_a_single_language() -> None:
    text = render_report(
        [_report((_case("only", language="zh"),))],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "## Results by language" in text
    assert "Reading-order errors" in text
    assert "RO err rate" in text
    assert "| ocr | zh | 1 |" in text


def test_language_table_rows_carry_reading_order_rate() -> None:
    en = _case("en-1", language="en", reading_order_errors=1, reading_order_error_rate=0.1)
    ja = _case(
        "ja-1",
        language="ja",
        direction="vertical-rl",
        tags=("vertical",),
        reading_order_errors=3,
        reading_order_error_rate=0.3,
        wer=None,
    )
    text = render_report(
        [_report((en, ja))],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    language_section = text.split("## Results by language", 1)[1]
    assert "| ocr | en | 1 |" in language_section and "| 1 | 0.1000 |" in language_section
    assert "| ocr | ja | 1 |" in language_section and "| 3 | 0.3000 |" in language_section


# ---------------------------------------------------------------------------
# report: direction strata and the dedicated vertical section
# ---------------------------------------------------------------------------


def test_direction_table_renders_with_rate_when_a_direction_is_declared() -> None:
    horizontal = _case("h", direction="horizontal-lr", reading_order_error_rate=0.0)
    vertical = _case(
        "v",
        language="ja",
        direction="vertical-rl",
        tags=("vertical",),
        reading_order_errors=4,
        reading_order_error_rate=0.4,
    )
    text = render_report(
        [_report((horizontal, vertical))],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "## Results by writing direction" in text
    assert "RO err rate" in text
    assert "| ocr | horizontal-lr | 1 |" in text
    assert "| ocr | vertical-rl | 1 |" in text and "| 4 | 0.4000 |" in text


def test_direction_table_still_omitted_when_no_case_declares_one() -> None:
    text = render_report(
        [_report((_case("x", direction=None),))],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "## Results by writing direction" not in text


def test_vertical_section_records_reading_order_rate_separately() -> None:
    vertical = _case(
        "v",
        language="ja",
        direction="vertical-rl",
        tags=("vertical",),
        cer=0.4,
        reading_order_errors=4,
        reading_order_error_rate=0.4,
    )
    horizontal = _case("h", direction="horizontal-lr", reading_order_error_rate=0.0)
    text = render_report(
        [_report((horizontal, vertical))],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "## Vertical text reading order" in text
    section = text.split("## Vertical text reading order", 1)[1]
    # Only the vertical case feeds this section, with its own error rate.
    assert "| ocr | 1 | 0.4000 | 4 | 0.4000 |" in section
    assert "vertical" in section.lower()


def test_vertical_section_absent_without_vertical_cases() -> None:
    text = render_report(
        [_report((_case("h", direction="horizontal-lr"),))],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "## Vertical text reading order" not in text


# ---------------------------------------------------------------------------
# report: per-case and aggregate tables carry the rate column
# ---------------------------------------------------------------------------


def test_variants_and_case_tables_show_ro_err_rate() -> None:
    case = _case("c1", reading_order_errors=2, reading_order_error_rate=0.25)
    report = _report((case,))
    aggregate = report.aggregate
    assert isinstance(aggregate, AggregateScore)
    text = render_report(
        [report],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
    )

    assert "RO err rate" in text
    assert "| ocr | 1 |" in text and "| 2 | 0.2500 |" in text  # variants row
    assert "| c1 | en | body |" in text and "| 2 | 0.2500 |" in text  # per-case row


# ---------------------------------------------------------------------------
# shipped artifacts: example manifest models en/ja, docs describe the rules
# ---------------------------------------------------------------------------


def test_example_manifest_models_english_two_column_and_japanese_vertical() -> None:
    root = Path(__file__).resolve().parent.parent / "benchmark" / "manifest.example.json"
    manifest = load_manifest(root)

    en_cases = [c for c in manifest.cases if c.language.strip().lower() == "en"]
    ja_cases = [c for c in manifest.cases if c.language.strip().lower() == "ja"]
    assert en_cases, "example manifest must model an English case"
    assert any("two-column" in t for c in en_cases for t in c.tags)
    assert ja_cases, "example manifest must model a Japanese case"
    assert any(c.direction == "vertical-rl" for c in ja_cases)
    assert all(c.source for c in manifest.cases)
    assert manifest.license


def test_benchmark_readme_documents_language_and_vertical_rules() -> None:
    readme = (Path(__file__).resolve().parent.parent / "benchmark" / "README.md").read_text(
        encoding="utf-8"
    )

    assert "Results by language" in readme
    assert "Vertical text reading order" in readme or "reading-order error rate" in readme
    assert "two-column" in readme
    assert "vertical" in readme


def test_main_readme_marks_en_and_ja_as_pending_verification() -> None:
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")

    assert "English" in readme and "Japanese" in readme
    # The multilingual claim must carry a verification status, not a bare claim.
    assert "ending" in readme  # "Pending" appears in the language status table


# ---------------------------------------------------------------------------
# render_report_json: machine-readable strata (schema v1)
# ---------------------------------------------------------------------------


def _json_report(cases: tuple[CaseScore, ...], engine: str = "ocr") -> dict:
    import json

    from scribeforge.benchmark.report import render_report_json

    text = render_report_json(
        [_report(cases, engine)],
        corpus="c",
        manifest_sha256="h",
        generated_at="now",
        manifest_license="CC0-1.0",
    )
    return json.loads(text)


def test_report_json_is_schema_v1_and_deterministic() -> None:
    import json

    from scribeforge.benchmark.report import render_report_json

    kwargs = {
        "corpus": "c",
        "manifest_sha256": "h",
        "generated_at": "now",
        "manifest_license": "CC0-1.0",
    }
    first = render_report_json([_report((_case("a"),))], **kwargs)
    second = render_report_json([_report((_case("a"),))], **kwargs)
    payload = json.loads(first)
    assert payload["schema_version"] == 1
    assert payload["corpus"] == "c"
    assert payload["manifest_license"] == "CC0-1.0"
    assert first == second
    assert first.endswith("\n")


def test_report_json_has_mandatory_by_language_strata_with_ro_rate() -> None:
    en = _case("en-1", language="en", reading_order_errors=1, reading_order_error_rate=0.1)
    ja = _case(
        "ja-1",
        language="ja",
        direction="vertical-rl",
        tags=("vertical",),
        reading_order_errors=3,
        reading_order_error_rate=0.3,
        wer=None,
    )
    payload = _json_report((en, ja))

    by_language = {
        row["language"]: row for variant in payload["variants"] for row in variant["by_language"]
    }
    assert set(by_language) == {"en", "ja"}
    assert by_language["en"]["cases"] == 1
    assert by_language["en"]["reading_order_errors"] == 1
    assert by_language["en"]["reading_order_error_rate"] == 0.1
    assert by_language["ja"]["reading_order_error_rate"] == 0.3
    # A language without cases has no row: the absence stays visible.
    assert "zh" not in by_language


def test_report_json_direction_and_vertical_sections() -> None:
    horizontal = _case("h", direction="horizontal-lr", reading_order_error_rate=0.0)
    vertical = _case(
        "v",
        language="ja",
        direction="vertical-rl",
        tags=("vertical",),
        cer=0.4,
        reading_order_errors=4,
        reading_order_error_rate=0.4,
    )
    payload = _json_report((horizontal, vertical))
    variant = payload["variants"][0]

    directions = {row["direction"]: row for row in variant["by_direction"]}
    assert directions["vertical-rl"]["reading_order_error_rate"] == 0.4
    assert directions["horizontal-lr"]["cases"] == 1

    vertical_rows = variant["vertical_reading_order"]
    assert len(vertical_rows) == 1
    assert vertical_rows[0]["cases"] == 1
    assert vertical_rows[0]["reading_order_errors"] == 4
    assert vertical_rows[0]["reading_order_error_rate"] == 0.4
    assert vertical_rows[0]["mean_cer"] == 0.4


def test_report_json_omits_direction_and_vertical_keys_when_undeclared() -> None:
    payload = _json_report((_case("x", direction=None),))
    variant = payload["variants"][0]

    assert variant["by_direction"] == []
    assert variant["vertical_reading_order"] == []
    # per-case rows still carry the rate
    assert variant["cases"][0]["reading_order_error_rate"] == 0.2
    assert variant["aggregate"]["reading_order_error_rate"] == 0.2


def test_cli_benchmark_report_json_writes_machine_readable_strata(tmp_path: Path) -> None:
    import io
    import json

    from PIL import Image

    from scribeforge.cli import run

    root = tmp_path / "corpus"
    root.mkdir()
    Image.new("RGB", (80, 120), "white").save(root / "page.png")
    (root / "truth.txt").write_text("first line\nsecond line", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        __import__("json").dumps(
            {
                "schema_version": 1,
                "license": "CC0-1.0",
                "cases": [
                    {
                        "id": "ja-v",
                        "image": "page.png",
                        "ground_truth": "truth.txt",
                        "language": "ja",
                        "tags": ["vertical"],
                        "direction": "vertical-rl",
                        "source": "synthetic test fixture",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out_md = tmp_path / "report.md"
    out_json = tmp_path / "report.json"

    class _FactoryEngine:
        name = "mineru"

        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            return PageOCRResult(
                page_index=page_index,
                engine=self.name,
                engine_version="1",
                lines=(
                    OCRLine("second line", BoundingBox(0.0, 0.5, 0.5, 0.4)),
                    OCRLine("first line", BoundingBox(0.0, 0.0, 0.5, 0.4)),
                ),
            )

    def factory(runtime_root: Path, backends) -> tuple:
        return (_FactoryEngine(),)

    code = run(
        [
            "benchmark",
            "--manifest",
            str(manifest),
            "--out",
            str(out_md),
            "--report-json",
            str(out_json),
            "--engine",
            "mineru",
        ],
        benchmark_engine_factory=factory,
        stdout=io.StringIO(),
    )

    assert code == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    variant = payload["variants"][0]
    assert variant["by_language"][0]["language"] == "ja"
    assert variant["vertical_reading_order"][0]["reading_order_error_rate"] == 0.5
    assert "## Vertical text reading order" in out_md.read_text(encoding="utf-8")
