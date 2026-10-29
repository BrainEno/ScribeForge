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


def _corpus(tmp_path: Path, ground_truth: str) -> tuple[Path, BenchmarkManifest]:
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
                        "language": "zh",
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
