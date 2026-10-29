"""Unit tests for the preprocessing benchmark harness.

The full run over the reference corpus is slow and marked ``benchmark``; these
tests cover the parts that must be correct regardless of whether the corpus is
present — deterministic degradation and honest aggregation.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "benchmark" / "analysis" / "preprocess_benchmark.py"


def _load():
    spec = importlib.util.spec_from_file_location("preprocess_benchmark", SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - import machinery
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    # Register before executing: ``from __future__ import annotations`` turns the
    # module's dataclass fields into strings, and dataclasses resolves them via
    # sys.modules[cls.__module__], which is None for an unregistered module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _load()


def _page() -> Image.Image:
    image = Image.new("L", (200, 260), 255)
    for y in range(20, 240, 12):
        for x in range(20, 180, 10):
            image.putpixel((x, y), 20)
            image.putpixel((x + 1, y), 20)
    return image


def test_speckle_degradation_is_deterministic(harness) -> None:
    """A seeded degradation must reproduce byte-for-byte, or numbers drift."""

    page = _page()

    first = harness._degrade(page, "speckled").tobytes()
    second = harness._degrade(page, "speckled").tobytes()

    assert first == second


def test_option_suffix_selects_the_same_degradation(harness) -> None:
    """``speckled+denoise`` must degrade exactly like ``speckled``."""

    page = _page()

    assert (
        harness._degrade(page, "speckled+denoise").tobytes()
        == harness._degrade(page, "speckled").tobytes()
    )


def test_clean_variant_does_not_modify_the_page(harness) -> None:
    page = _page()

    assert harness._degrade(page, "clean").tobytes() == page.convert("L").tobytes()


def test_unknown_variant_is_rejected(harness) -> None:
    with pytest.raises(ValueError, match="unknown degradation variant"):
        harness._degrade(_page(), "melted")


def test_summarize_aggregates_operations_and_worst_residual(harness) -> None:
    rows = [
        {
            "variant": "skew-3deg",
            "applied": ["deskew"],
            "separability_degraded": 200.0,
            "separability_corrected": 210.0,
            "residual_skew_degrees": 0.0,
        },
        {
            "variant": "skew-3deg",
            "applied": ["deskew", "deshadow"],
            "separability_degraded": 180.0,
            "separability_corrected": 200.0,
            "residual_skew_degrees": -0.5,
        },
    ]

    summary = harness.summarize(rows)["skew-3deg"]

    assert summary["pages"] == 2
    assert summary["operations_seen"] == ["deshadow", "deskew"]
    assert summary["mean_separability_degraded"] == 190.0
    assert summary["max_abs_residual_skew"] == 0.5


@pytest.mark.benchmark
def test_preprocess_benchmark_reports_recovery_on_the_reference_corpus() -> None:
    """The measured claims in docs/preprocessing.md, re-checked on real pages."""

    harness = _load()
    images = sorted((harness.CORPUS_DIR / "images").glob("*.png"))
    if not images:
        pytest.skip("reference corpus images are not present")
    rows = harness.evaluate_page(images[0], harness.VARIANTS)
    by_variant = {row["variant"]: row for row in rows}

    clean = by_variant["clean"]
    assert clean["applied"] == [], "a healthy 300 DPI scan must not be touched"

    skewed = by_variant["skew-3deg"]
    assert "deskew" in skewed["applied"]
    assert abs(skewed["residual_skew_degrees"]) <= 0.5

    shadowed = by_variant["spine-shadow"]
    assert "deshadow" in shadowed["applied"]
    assert shadowed["separability_corrected"] > shadowed["separability_degraded"]

    faded = by_variant["faded"]
    assert "contrast" in faded["applied"]
    assert faded["separability_corrected"] > faded["separability_degraded"]
