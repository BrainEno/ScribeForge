"""Tests for the PDF characteristics analysis used in the ancient-book capability assessment."""

from __future__ import annotations

from pathlib import Path

from benchmark.analysis.pdf_characteristics import full_analysis


def test_full_analysis_returns_valid_structure(tmp_path: Path) -> None:
    """full_analysis should produce a valid JSON-serializable structure."""
    # Create a minimal PDF fixture for testing

    # Use the actual benchmark PDF if available
    pdf_path = Path("benchmark/V..pdf")
    if not pdf_path.exists():
        return  # Skip test if PDF not available

    result = full_analysis(str(pdf_path), sample_pages=[0, 1], dpi=150)

    assert "pdf_path" in result
    assert "total_pages" in result
    assert "pages" in result
    assert "aggregates" in result
    assert result["total_pages"] >= 2
    assert len(result["pages"]) == 2


def test_full_analysis_page_stats(tmp_path: Path) -> None:
    """Each page entry should contain all expected statistics."""
    pdf_path = Path("benchmark/V..pdf")
    if not pdf_path.exists():
        return

    result = full_analysis(str(pdf_path), sample_pages=[5], dpi=150)
    page = result["pages"][0]

    assert "width_px" in page
    assert "height_px" in page
    assert "mean_brightness" in page
    assert "std_brightness" in page
    assert "dark_pixel_ratio" in page
    assert "estimated_png_kb" in page
    assert 0 <= page["mean_brightness"] <= 255
    assert 0 <= page["dark_pixel_ratio"] <= 1


def test_full_analysis_aggregates(tmp_path: Path) -> None:
    """Aggregates should summarize the sampled pages correctly."""
    pdf_path = Path("benchmark/V..pdf")
    if not pdf_path.exists():
        return

    result = full_analysis(str(pdf_path), sample_pages=list(range(10)), dpi=150)
    agg = result["aggregates"]

    assert "mean_brightness" in agg
    assert "mean_dark_pixel_ratio" in agg
    assert "estimated_full_book_png_mb" in agg
    assert 0 <= agg["mean_brightness"] <= 255
    assert 0 <= agg["mean_dark_pixel_ratio"] <= 1


def test_full_analysis_page_type_classification(tmp_path: Path) -> None:
    """The cover page (index 0) has a very low dark pixel ratio, indicating
    a colored/non-text page, while text pages have higher dark ratios."""
    pdf_path = Path("benchmark/V..pdf")
    if not pdf_path.exists():
        return

    result = full_analysis(str(pdf_path), sample_pages=[0, 5], dpi=300)
    cover = result["pages"][0]
    text_page = result["pages"][1]

    # Cover: very few dark pixels (mostly uniform color)
    assert cover["dark_pixel_ratio"] < 0.01
    # Text page: measurable dark pixel ratio from actual text
    assert text_page["dark_pixel_ratio"] > 0.01


def test_benchmark_corpus_manifest_valid() -> None:
    """The V benchmark corpus manifest should be valid."""
    from scribeforge.benchmark.corpus import validate_corpus
    from scribeforge.benchmark.manifest import load_manifest

    manifest = load_manifest(Path("benchmark/corpus/v/manifest.json"))
    assert manifest.cases

    problems = validate_corpus(manifest, Path("benchmark/corpus/v"))
    assert problems == (), f"Corpus validation failed: {problems}"


def test_benchmark_corpus_pages_exist() -> None:
    """All referenced corpus images and ground truth files must exist."""
    manifest_path = Path("benchmark/corpus/v/manifest.json")
    if not manifest_path.exists():
        return

    from scribeforge.benchmark.manifest import load_manifest
    manifest = load_manifest(manifest_path)
    root = Path("benchmark/corpus/v")

    for case in manifest.cases:
        image_path = root / case.image
        truth_path = root / case.ground_truth
        assert image_path.exists(), f"Missing corpus image: {image_path}"
        assert truth_path.exists(), f"Missing ground truth: {truth_path}"
        assert truth_path.read_text(encoding="utf-8").strip(), f"Blank ground truth: {truth_path}"
