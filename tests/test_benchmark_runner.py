from pathlib import Path

from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest
from scribeforge.benchmark.runner import run_benchmark
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult


class FakeEngine:
    name = "fake"

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        assert Path(image_path).name == "page.png"
        return PageOCRResult(
            page_index=page_index,
            engine=self.name,
            engine_version="1",
            lines=(OCRLine("你好,世界。", BoundingBox(0, 0, 1, 1)),),
        )


def test_runner_scores_case_and_preserves_identity(tmp_path) -> None:
    (tmp_path / "page.png").write_bytes(b"fixture")
    (tmp_path / "truth.txt").write_text("你好，世界。", encoding="utf-8")
    manifest = BenchmarkManifest(
        1,
        (BenchmarkCase("case-1", "page.png", "truth.txt", "zh", ("body",)),),
    )

    report = run_benchmark(manifest, tmp_path, FakeEngine())

    assert report.engine == "fake"
    assert report.cases[0].case_id == "case-1"
    assert report.cases[0].cer > 0
    assert report.cases[0].punctuation_accuracy == 0.5
    assert (report.cases[0].character_errors, report.cases[0].reference_characters) == (
        1,
        6,
    )
    assert report.cases[0].word_errors is None
    assert report.cases[0].reference_words is None
    assert report.aggregate.micro_cer == 1 / 6
    assert report.aggregate.micro_wer is None
    assert report.aggregate.case_count == 1


def test_runner_records_language_tags_and_timing(tmp_path: Path) -> None:
    (tmp_path / "page.png").write_bytes(b"fixture")
    (tmp_path / "truth.txt").write_text("你好,世界。", encoding="utf-8")
    manifest = BenchmarkManifest(
        1,
        (BenchmarkCase("case-1", "page.png", "truth.txt", "zh", ("body", "punctuation")),),
    )
    ticks = iter((10.0, 12.5))

    report = run_benchmark(manifest, tmp_path, FakeEngine(), clock=lambda: next(ticks))

    case = report.cases[0]
    assert (case.language, case.tags) == ("zh", ("body", "punctuation"))
    assert case.elapsed_seconds == 2.5
    assert report.aggregate.elapsed_seconds == 2.5
    assert report.aggregate.pages_per_minute == 24.0


def test_runner_reports_zero_pages_per_minute_for_an_instant_run(
    tmp_path: Path,
) -> None:
    (tmp_path / "page.png").write_bytes(b"fixture")
    (tmp_path / "truth.txt").write_text("你好,世界。", encoding="utf-8")
    manifest = BenchmarkManifest(
        1, (BenchmarkCase("case-1", "page.png", "truth.txt", "zh", ("body",)),)
    )

    report = run_benchmark(manifest, tmp_path, FakeEngine(), clock=lambda: 5.0)

    assert report.cases[0].elapsed_seconds == 0.0
    assert report.aggregate.pages_per_minute == 0.0


class DroppingEngine:
    """Returns only the first reference line, as if one line region was dropped."""

    name = "dropper"

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        return PageOCRResult(
            page_index=page_index,
            engine=self.name,
            engine_version="1",
            lines=(
                OCRLine("甲乙丙", BoundingBox(0.05, 0.05, 0.9, 0.4)),
                # No overlap with either declared reference box: spatially extra.
                OCRLine("完全不同的行", BoundingBox(0.05, 0.50, 0.9, 0.04)),
            ),
        )


def test_runner_counts_spatial_lines_only_with_declared_reference_geometry(
    tmp_path: Path,
) -> None:
    (tmp_path / "page.png").write_bytes(b"fixture")
    (tmp_path / "truth.txt").write_text("甲乙丙\n丁戊己", encoding="utf-8")
    manifest = BenchmarkManifest(
        1, (BenchmarkCase("case-1", "page.png", "truth.txt", "zh", ("body",)),)
    )
    layout = {
        "case-1": (
            BoundingBox(0.05, 0.05, 0.9, 0.4),
            BoundingBox(0.05, 0.55, 0.9, 0.4),
        )
    }

    without_layout = run_benchmark(manifest, tmp_path, DroppingEngine())
    assert without_layout.cases[0].spatial_missing_lines is None
    assert without_layout.cases[0].spatial_extra_lines is None
    assert without_layout.aggregate.spatial_missing_lines is None

    report = run_benchmark(manifest, tmp_path, DroppingEngine(), reference_layout=layout)
    # The second reference box has no overlapping hypothesis box, and the
    # hypothesis's second line has no overlapping reference box.
    assert (report.cases[0].spatial_missing_lines, report.cases[0].spatial_extra_lines) == (1, 1)
    assert (report.aggregate.spatial_missing_lines, report.aggregate.spatial_extra_lines) == (1, 1)
    # Strict LCS counts stay text-based and independent of geometry.
    assert (report.cases[0].missing_lines, report.cases[0].extra_lines) == (1, 1)


def test_chinese_case_end_to_end_reports_cer_and_marks_a_one_char_error_as_changed(
    tmp_path: Path,
) -> None:
    """#80 runtime regression: Chinese WER is N/A with an explanation, and a
    one-character line difference is Changed, never a missing line."""

    from scribeforge.benchmark.report import render_report

    class OneCharOffEngine:
        name = "one-char-off"

        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            # Both ground-truth lines, the first with one substituted character.
            return PageOCRResult(
                page_index=page_index,
                engine=self.name,
                engine_version="1",
                lines=(
                    OCRLine("你好,世界。", BoundingBox(0, 0, 1, 0.5)),
                    OCRLine("第二行文字", BoundingBox(0, 0.5, 1, 0.5)),
                ),
            )

    (tmp_path / "page.png").write_bytes(b"fixture")
    (tmp_path / "truth.txt").write_text("你好，世界。\n第二行文字", encoding="utf-8")
    manifest = BenchmarkManifest(
        1,
        (BenchmarkCase("case-1", "page.png", "truth.txt", "zh-Hant", ("body",)),),
    )

    report = run_benchmark(manifest, tmp_path, OneCharOffEngine())
    case = report.cases[0]
    # Whitespace tokenization is not applicable to Chinese: WER is N/A, CER is primary.
    assert case.wer is None
    assert case.word_tokenizer == "not-applicable"
    assert case.word_errors is None and case.reference_words is None
    assert (case.character_errors, case.reference_characters) == (1, 12)
    # The one-character difference is a changed line, not a missing/extra line.
    assert (case.missing_lines, case.extra_lines, case.changed_lines) == (1, 1, 1)
    assert (case.net_missing_lines, case.net_extra_lines) == (0, 0)

    text = render_report((report,), corpus="c", manifest_sha256="h", generated_at="now")
    # The report explains what a Chinese WER above 1.0 would mean and why it is N/A.
    assert "tokenizer artifact" in text
    assert "N/A (`not-applicable`)" in text
    # Per-case row shows the raw CER counts and the changed-line evidence.
    assert "1 / 12 chars" in text
    assert "| 1 | 1 | 1 | 0 | 0 |" in text
