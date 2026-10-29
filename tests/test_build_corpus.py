"""Corpus builder: real pages in, blank transcriptions that block scoring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scribeforge.benchmark.corpus import validate_corpus
from scribeforge.benchmark.manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_PDF = REPO_ROOT / "benchmark" / "V..pdf"


def _builder():
    sys.path.insert(0, str(REPO_ROOT / "benchmark" / "analysis"))
    try:
        import build_corpus  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return build_corpus


def test_builder_rejects_duplicate_pages(tmp_path: Path) -> None:
    builder = _builder()

    with pytest.raises(ValueError, match="unique"):
        builder.build_corpus(
            pdf=tmp_path / "missing.pdf",
            out=tmp_path / "corpus",
            name="x",
            pages=(1, 1),
            direction=None,
            language="zh-Hant",
            dpi=100,
            source_note="",
        )


def test_builder_rejects_an_unknown_direction(tmp_path: Path) -> None:
    builder = _builder()

    with pytest.raises(ValueError, match="unknown direction"):
        builder.build_corpus(
            pdf=tmp_path / "missing.pdf",
            out=tmp_path / "corpus",
            name="x",
            pages=(1,),
            direction="right-to-left",
            language="zh-Hant",
            dpi=100,
            source_note="",
        )


@pytest.mark.benchmark
def test_builder_renders_real_pages_and_blocks_scoring_until_transcribed(
    tmp_path: Path,
) -> None:
    """A built corpus must be scored only after a human writes the transcription."""

    if not BENCHMARK_PDF.is_file():
        pytest.skip("benchmark PDF is not available")

    builder = _builder()
    out = tmp_path / "corpus"

    manifest = builder.build_corpus(
        pdf=BENCHMARK_PDF,
        out=out,
        name="t",
        pages=(10, 14),
        direction="vertical-rl",
        language="zh-Hant",
        dpi=100,
        source_note="test corpus",
        tags=("body", "vertical"),
    )

    assert [case["id"] for case in manifest["cases"]] == ["t-010", "t-014"]
    for case in manifest["cases"]:
        assert (out / str(case["image"])).is_file()
        assert case["direction"] == "vertical-rl"

    loaded = load_manifest(out / "manifest.json")
    problems = validate_corpus(loaded, out)

    # Empty transcriptions are the point: a page cannot score against itself.
    assert {problem.problem.split(":")[0] for problem in problems} == {
        "ground truth is blank"
    }

    (out / "ground_truth" / "t-010.txt").write_text("甲\n", encoding="utf-8")
    (out / "ground_truth" / "t-014.txt").write_text("乙\n", encoding="utf-8")
    assert validate_corpus(load_manifest(out / "manifest.json"), out) == ()


@pytest.mark.benchmark
def test_builder_does_not_overwrite_an_existing_transcription(tmp_path: Path) -> None:
    if not BENCHMARK_PDF.is_file():
        pytest.skip("benchmark PDF is not available")

    builder = _builder()
    out = tmp_path / "corpus"
    truth = out / "ground_truth" / "t-010.txt"
    truth.parent.mkdir(parents=True)
    truth.write_text("已校订的转写\n", encoding="utf-8")

    builder.build_corpus(
        pdf=BENCHMARK_PDF,
        out=out,
        name="t",
        pages=(10,),
        direction=None,
        language="zh-Hant",
        dpi=100,
        source_note="",
    )

    assert truth.read_text(encoding="utf-8") == "已校订的转写\n"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert "direction" not in manifest["cases"][0]
