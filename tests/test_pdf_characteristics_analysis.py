"""Tests for the benchmark PDF analysis tooling and the V corpus.

The rendering tests drive ``benchmark/analysis/pdf_characteristics.py``, which is a
standalone measurement script rather than part of the installed package, so it is
loaded by path. Importing it as ``benchmark.analysis...`` only works when the
current directory happens to be on ``sys.path`` (``python -m pytest``); plain
``pytest`` -- which is what CI runs -- does not do that.

The rendering tests are marked ``benchmark``: they rasterize real scanned pages and
are measurement, not unit tests. The corpus structure checks stay unmarked so CI
still catches a broken manifest or a missing corpus file.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_PDF = REPO_ROOT / "benchmark" / "V..pdf"
CORPUS_DIR = REPO_ROOT / "benchmark" / "corpus" / "v"
CHARACTERISTICS_SCRIPT = REPO_ROOT / "benchmark" / "analysis" / "pdf_characteristics.py"


def _load_characteristics() -> ModuleType:
    """Load the analysis script by path, independent of ``sys.path`` and CWD."""

    spec = importlib.util.spec_from_file_location(
        "scribeforge_benchmark_pdf_characteristics", CHARACTERISTICS_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {CHARACTERISTICS_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _characteristics():
    if not CHARACTERISTICS_SCRIPT.is_file():
        pytest.skip("benchmark analysis script is not present")
    return _load_characteristics()


def _require_pdf() -> Path:
    if not BENCHMARK_PDF.is_file():
        pytest.skip(f"benchmark source PDF is not present: {BENCHMARK_PDF.name}")
    return BENCHMARK_PDF


@pytest.mark.benchmark
def test_full_analysis_returns_valid_structure() -> None:
    """full_analysis should produce a valid JSON-serializable structure."""

    module = _characteristics()
    pdf_path = _require_pdf()

    result = module.full_analysis(str(pdf_path), sample_pages=[0, 1], dpi=150)

    assert "pdf_path" in result
    assert "total_pages" in result
    assert "pages" in result
    assert "aggregates" in result
    assert result["total_pages"] >= 2
    assert len(result["pages"]) == 2


@pytest.mark.benchmark
def test_full_analysis_page_stats() -> None:
    """Each page entry should contain all expected statistics."""

    module = _characteristics()
    pdf_path = _require_pdf()

    result = module.full_analysis(str(pdf_path), sample_pages=[5], dpi=150)
    page = result["pages"][0]

    assert "width_px" in page
    assert "height_px" in page
    assert "mean_brightness" in page
    assert "std_brightness" in page
    assert "dark_pixel_ratio" in page
    assert "estimated_png_kb" in page
    assert 0 <= page["mean_brightness"] <= 255
    assert 0 <= page["dark_pixel_ratio"] <= 1


@pytest.mark.benchmark
def test_full_analysis_aggregates() -> None:
    """Aggregates should summarize the sampled pages correctly."""

    module = _characteristics()
    pdf_path = _require_pdf()

    result = module.full_analysis(str(pdf_path), sample_pages=list(range(10)), dpi=150)
    aggregates = result["aggregates"]

    assert "mean_brightness" in aggregates
    assert "mean_dark_pixel_ratio" in aggregates
    assert "estimated_full_book_png_mb" in aggregates
    assert 0 <= aggregates["mean_brightness"] <= 255
    assert 0 <= aggregates["mean_dark_pixel_ratio"] <= 1


@pytest.mark.benchmark
def test_full_analysis_page_type_classification() -> None:
    """The cover has a very low dark pixel ratio; a text page does not."""

    module = _characteristics()
    pdf_path = _require_pdf()

    result = module.full_analysis(str(pdf_path), sample_pages=[0, 5], dpi=300)
    cover = result["pages"][0]
    text_page = result["pages"][1]

    assert cover["dark_pixel_ratio"] < 0.01
    assert text_page["dark_pixel_ratio"] > 0.01


def test_benchmark_corpus_manifest_valid() -> None:
    """The V benchmark corpus manifest and every case it references are valid."""

    from scribeforge.benchmark.corpus import validate_corpus
    from scribeforge.benchmark.manifest import load_manifest

    if not (CORPUS_DIR / "manifest.json").is_file():
        pytest.skip("V benchmark corpus is not present")

    manifest = load_manifest(CORPUS_DIR / "manifest.json")
    assert manifest.cases

    problems = validate_corpus(manifest, CORPUS_DIR)
    assert problems == (), f"Corpus validation failed: {problems}"


def test_benchmark_corpus_pages_exist() -> None:
    """All referenced corpus images and ground truth files must exist and be non-empty."""

    from scribeforge.benchmark.manifest import load_manifest

    manifest_path = CORPUS_DIR / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("V benchmark corpus is not present")

    manifest = load_manifest(manifest_path)
    for case in manifest.cases:
        image_path = CORPUS_DIR / case.image
        truth_path = CORPUS_DIR / case.ground_truth
        assert image_path.exists(), f"Missing corpus image: {case.image}"
        assert truth_path.exists(), f"Missing ground truth: {case.ground_truth}"
        assert truth_path.read_text(encoding="utf-8").strip(), (
            f"Blank ground truth: {case.ground_truth}"
        )
