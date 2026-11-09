"""Oracle ceiling: the pipeline's own text loss, measured with zero OCR error."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.benchmark.manifest import BenchmarkCase, BenchmarkManifest, load_manifest
from scribeforge.benchmark.metrics import scoring_text
from scribeforge.benchmark.oracle import OracleEngine
from scribeforge.benchmark.pipeline_runner import run_pipeline_benchmark
from scribeforge.benchmark.runner import run_benchmark
from scribeforge.domain.ocr import PageDirection, PageOCRResult


def _corpus(
    tmp_path: Path, ground_truth: str, *, language: str = "zh"
) -> tuple[Path, BenchmarkManifest]:
    Image.new("RGB", (100, 120), "white").save(tmp_path / "page.png")
    (tmp_path / "truth.txt").write_text(ground_truth, encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "case-1",
                        "image": "page.png",
                        "ground_truth": "truth.txt",
                        "language": language,
                        "tags": ["body"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path, load_manifest(manifest_path)


def test_scoring_text_ignores_file_formatting_not_content() -> None:
    """A ground-truth file's trailing newline is not a recognition error."""

    assert scoring_text("甲乙\n丙丁\n") == "甲乙\n丙丁"
    assert scoring_text("甲乙\r\n丙丁\r\n") == "甲乙\n丙丁"
    assert scoring_text("甲乙  \n丙丁") == "甲乙\n丙丁"
    assert scoring_text("\n甲乙\n丙丁\n\n") == "甲乙\n丙丁"
    # Blank lines inside a page are structure and must survive.
    assert scoring_text("甲乙\n\n丙丁") == "甲乙\n\n丙丁"


def test_oracle_engine_scores_a_perfect_zero(tmp_path: Path) -> None:
    root, manifest = _corpus(tmp_path, "甲乙\n丙丁\n")
    engine = OracleEngine("oracle")
    engine.register(root / "page.png", "甲乙\n丙丁\n")

    report = run_benchmark(manifest, root, engine)

    assert report.cases[0].cer == 0.0
    assert report.cases[0].missing_lines == 0
    assert report.cases[0].extra_lines == 0


def test_single_engine_scores_language_appropriate_wer_and_changed_lines(
    tmp_path: Path,
) -> None:
    chinese_root, chinese_manifest = _corpus(tmp_path, "乌鸦\n喝水")
    chinese_engine = OracleEngine("oracle")
    chinese_engine.register(chinese_root / "page.png", "鸟鸦\n喝水")

    chinese = run_benchmark(chinese_manifest, chinese_root, chinese_engine).cases[0]
    assert chinese.wer is None
    assert chinese.word_tokenizer == "not-applicable"
    assert (chinese.missing_lines, chinese.extra_lines) == (1, 1)
    assert chinese.changed_lines == 1
    assert (chinese.net_missing_lines, chinese.net_extra_lines) == (0, 0)

    english_dir = tmp_path / "english"
    english_dir.mkdir()
    english_root, english_manifest = _corpus(english_dir, "one two", language="en-US")
    english_engine = OracleEngine("oracle")
    english_engine.register(english_root / "page.png", "one three")

    english_report = run_benchmark(english_manifest, english_root, english_engine)
    assert english_report.cases[0].wer == 0.5
    assert english_report.cases[0].word_tokenizer == "whitespace-v1"
    assert english_report.aggregate.mean_wer == 0.5
    assert english_report.aggregate.wer_case_count == 1


def test_oracle_engine_refuses_a_page_it_has_no_reference_for(tmp_path: Path) -> None:
    root, _manifest = _corpus(tmp_path, "甲乙\n")
    engine = OracleEngine("oracle")

    with pytest.raises(KeyError, match="no reference text"):
        engine.analyze_page(str(root / "page.png"), 0)


def test_pipeline_ceiling_preserves_every_line_it_is_given(tmp_path: Path) -> None:
    """With recognition error at zero, any remaining CER is pipeline loss."""

    root, manifest = _corpus(tmp_path, "甲乙丙\n丁戊己\n")
    primary = OracleEngine("oracle")
    secondary = OracleEngine("oracle")
    primary.register(root / "page.png", "甲乙丙\n丁戊己\n")
    secondary.register(root / "page.png", "甲乙丙\n丁戊己\n")

    report = run_pipeline_benchmark(
        manifest, root, primary, secondary, label="oracle-ceiling"
    )

    assert report.engine == "oracle-ceiling"
    assert report.cases[0].cer == 0.0
    assert report.cases[0].accepted_pairs == 2
    assert report.cases[0].unresolved_pairs == 0


def test_cli_oracle_ceiling_writes_both_variants(tmp_path: Path) -> None:
    from scribeforge.cli import run

    root, _manifest = _corpus(tmp_path, "甲乙\n丙丁\n")
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(root / "manifest.json"),
            "--out",
            str(out_path),
            "--oracle-ceiling",
        ]
    )

    text = out_path.read_text(encoding="utf-8")
    assert code == 0
    assert "| oracle | 6" not in text  # this corpus has one case, not six
    assert "| oracle |" in text
    assert "| oracle-ceiling |" in text
    assert "- corpus content sha256: `" in text
    # Each of the two variants has an overall row and a manifest-tag row.
    assert text.count("| N/A (0 / 1) | N/A |") == 4
    assert (
        text.count("| case-1 | zh | body | 0.0000 (0 / 5 chars) [0.0000, 0.4345] | N/A (`not-applicable`) |")
        == 2
    )
    # The oracle variant declares reference geometry, so its spatial counts are
    # real zeros (the ceiling); the pipeline variant has none and shows '-'.
    assert "| Spatial Missing | Spatial Extra |" in text
    # Full variant rows: the oracle row carries real spatial 0/0 counts, the
    # pipeline row has no declared geometry for accepted text and shows '-'.
    assert (
        "| oracle | 1 | 0.0000 | 0.0000 (0 / 5) [0.0000, 0.4345] | N/A (0 / 1) | N/A | "
        "1.0000 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | - | - |" in text
    )
    oracle_ceiling_rows = [
        line for line in text.splitlines() if line.startswith("| oracle-ceiling | 1 |")
    ]
    assert oracle_ceiling_rows and all("| - | - | 0 | 2 |" in row for row in oracle_ceiling_rows)
    assert "| LCS Missing | LCS Extra | Changed | Net Missing | Net Extra |" in text
    assert "`whitespace-v1` preserves case and punctuation" in text


def test_cli_oracle_ceiling_refuses_engine_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from scribeforge.cli import run

    root, _manifest = _corpus(tmp_path, "甲乙\n")
    out_path = tmp_path / "report.md"

    code = run(
        [
            "benchmark",
            "--manifest",
            str(root / "manifest.json"),
            "--out",
            str(out_path),
            "--oracle-ceiling",
            "--engine",
            "mineru",
        ]
    )

    assert code == 2
    assert "cannot be combined" in capsys.readouterr().err
    assert not out_path.exists()


def test_benchmark_case_manifest_round_trips_for_the_oracle_root(tmp_path: Path) -> None:
    root, manifest = _corpus(tmp_path, "甲乙\n")

    assert manifest.cases == (BenchmarkCase("case-1", "page.png", "truth.txt", "zh", ("body",)),)
    assert (root / "page.png").is_file()


VERTICAL_CORPUS = Path(__file__).resolve().parent.parent / "benchmark" / "corpus" / "guji"


def test_vertical_oracle_places_columns_right_to_left() -> None:
    """A vertical page must be laid out as columns, not as rows.

    Stacking the reference text as rows would exercise the horizontal pairing
    path while the case claims to be vertical, so the ceiling would say nothing
    about vertical support.
    """

    from scribeforge.domain.ocr import PageDirection

    engine = OracleEngine("oracle")
    engine.register(
        Path("/tmp/does-not-matter.png"),
        "第一列\n第二列",
        direction=PageDirection.VERTICAL_RL,
    )

    result = engine.analyze_page("/tmp/does-not-matter.png", 0)

    right, left = result.lines
    assert right.box.x > left.box.x
    # Columns are tall and narrow: that is what makes the page read as vertical.
    assert right.box.height > right.box.width
    assert result.direction is PageDirection.VERTICAL_RL
    assert result.direction_declared is True


@pytest.mark.benchmark
def test_real_vertical_corpus_scores_zero_at_the_ceiling() -> None:
    """The vertical pipeline preserves text on real woodblock pages.

    Three genuine vertical scans (public-domain 四庫全書 woodblock, hand
    transcription) run through alignment, risk, decisions and assembly. A zero
    ceiling means the vertical path - column pairing by X, right-to-left order,
    column splicing - does not lose or reorder text.
    """

    manifest_path = VERTICAL_CORPUS / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("vertical corpus is not available")

    manifest = load_manifest(manifest_path)
    primary = OracleEngine("oracle")
    secondary = OracleEngine("oracle")
    for case in manifest.cases:
        image = (VERTICAL_CORPUS / case.image).resolve()
        text = (VERTICAL_CORPUS / case.ground_truth).read_text(encoding="utf-8")
        for engine in (primary, secondary):
            engine.register(image, text, direction=PageDirection(case.direction or "vertical-rl"))

    assert all(case.direction == "vertical-rl" for case in manifest.cases)
    report = run_pipeline_benchmark(
        manifest, VERTICAL_CORPUS, primary, secondary, label="oracle-ceiling"
    )

    assert report.aggregate.mean_cer == 0.0
    assert report.aggregate.unresolved_pairs == 0


@pytest.mark.benchmark
def test_real_vertical_corpus_cer_is_sensitive_to_column_order() -> None:
    """The zero above must come from correct ordering, not from luck.

    Registering the same pages with the transcription reversed leaves every
    character intact and only changes which column holds which text. If the
    pipeline really orders columns right to left, the score must collapse.
    """

    manifest_path = VERTICAL_CORPUS / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("vertical corpus is not available")

    manifest = load_manifest(manifest_path)
    primary = OracleEngine("oracle")
    secondary = OracleEngine("oracle")
    for case in manifest.cases:
        image = (VERTICAL_CORPUS / case.image).resolve()
        text = (VERTICAL_CORPUS / case.ground_truth).read_text(encoding="utf-8")
        reversed_text = "\n".join(reversed(text.splitlines()))
        for engine in (primary, secondary):
            engine.register(
                image, reversed_text, direction=PageDirection.VERTICAL_RL
            )

    report = run_pipeline_benchmark(
        manifest, VERTICAL_CORPUS, primary, secondary, label="oracle-ceiling"
    )

    assert report.aggregate.mean_cer > 0.2


@pytest.mark.benchmark
def test_geometry_alone_detects_vertical_on_the_real_corpus() -> None:
    """Direction detection must work without any engine declaration.

    The oracle declares its direction, so this test strips the declaration and
    asks the production detector to recover it from the column boxes alone. It is
    the only check in the repository that runs the detector on pages whose
    layout comes from a real woodblock scan rather than a fixture.
    """

    from scribeforge.domain.direction import detect_page_direction

    manifest_path = VERTICAL_CORPUS / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("vertical corpus is not available")

    manifest = load_manifest(manifest_path)
    for case in manifest.cases:
        image = (VERTICAL_CORPUS / case.image).resolve()
        text = (VERTICAL_CORPUS / case.ground_truth).read_text(encoding="utf-8")
        engine = OracleEngine("oracle")
        engine.register(image, text, direction=PageDirection.VERTICAL_RL)
        declared = engine.analyze_page(str(image), 0)
        undeclared = PageOCRResult(
            declared.page_index,
            declared.engine,
            declared.engine_version,
            declared.lines,
        )

        assert detect_page_direction(undeclared) is PageDirection.VERTICAL_RL, case.id


def test_oracle_resolves_isolated_copy_by_content_and_refuses_ambiguous_images(tmp_path: Path) -> None:
    import shutil

    original = tmp_path / "original.png"
    Image.new("RGB", (16, 16), "white").save(original)
    copy = tmp_path / "copy.png"
    shutil.copyfile(original, copy)
    oracle = OracleEngine("oracle")
    oracle.register(original, "甲")
    assert oracle.analyze_page(str(copy), 0).lines[0].text == "甲"
    oracle.register(copy, "乙")
    assert oracle.analyze_page(str(original), 0).lines[0].text == "甲"
    assert oracle.analyze_page(str(copy), 0).lines[0].text == "乙"
    other = tmp_path / "unregistered.png"
    shutil.copyfile(original, other)
    with pytest.raises(ValueError, match="ambiguous"):
        oracle.analyze_page(str(other), 0)


def test_oracle_copy_matches_registration_snapshot_not_later_file_contents(tmp_path: Path) -> None:
    import shutil

    original = tmp_path / "original.png"
    copy = tmp_path / "copy.png"
    Image.new("RGB", (10, 10), "white").save(original)
    oracle = OracleEngine("oracle")
    oracle.register(original, "甲")
    Image.new("RGB", (10, 10), "black").save(original)
    shutil.copyfile(original, copy)
    with pytest.raises(KeyError, match="no reference text"):
        oracle.analyze_page(str(copy), 0)
