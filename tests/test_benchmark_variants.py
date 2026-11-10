"""Deterministic A/B/C/D variant comparison over a synthetic corpus (#81).

The historical report could only score single-engine variants on four pages.
This harness makes the C/D *benefit* measurable without real OCR runtimes: a
committed, seeded synthetic corpus plus scripted engines let anyone re-run the
exact comparison and check the measured numbers, instead of trusting a claim.

Measured relationships asserted here (all deterministic):

- variant A (primary engine only) false-accepts its scripted error lines, so
  its CER is above zero;
- variant B (secondary engine only) reads the reference verbatim, CER 0;
- variant C (dual-engine decisions, no VLM) never accepts a wrong line
  (false-accepted pairs == 0) but leaves every disputed pair unresolved for a
  human;
- variant D (C + corroborating VLM) resolves those disputes toward the
  corroborated engine: CER 0, unresolved 0, still zero false accepts.
"""

from __future__ import annotations

import pytest

from scribeforge.benchmark.manifest import load_manifest
from scribeforge.benchmark.runner import run_benchmark
from scribeforge.benchmark.variants import (
    CORRUPTED_LINE_INDEXES,
    build_synthetic_corpus,
    run_variant_comparison,
    scripted_engines,
)

pytestmark = pytest.mark.benchmark


def test_variant_comparison_measures_all_four_variants(tmp_path) -> None:
    manifest_path = build_synthetic_corpus(tmp_path / "corpus")
    manifest = load_manifest(manifest_path)
    root = manifest_path.parent

    comparison = run_variant_comparison(root, manifest_path)
    by_engine = {report.engine: report for report in comparison.reports}
    assert set(by_engine) == {"mineru", "paddleocr", "dual-engine", "dual-engine+vlm"}

    # A single engine that errs on scripted lines emits wrong text.
    assert by_engine["mineru"].aggregate.mean_cer > 0.0
    # The perfect scripted engine scores zero.
    assert by_engine["paddleocr"].aggregate.mean_cer == 0.0

    # C: disagreements stay unresolved for humans, and no accepted pair is wrong.
    variant_c = by_engine["dual-engine"].aggregate
    assert variant_c.false_accepted_pairs == 0
    assert variant_c.unresolved_pairs == len(manifest.cases) * len(CORRUPTED_LINE_INDEXES)
    assert variant_c.accepted_pairs is not None and variant_c.accepted_pairs > 0

    # D: the corroborating VLM resolves exactly the disputed pairs, correctly.
    variant_d = by_engine["dual-engine+vlm"].aggregate
    assert variant_d.mean_cer == 0.0
    assert variant_d.unresolved_pairs == 0
    assert variant_d.false_accepted_pairs == 0
    assert variant_d.accepted_pairs == variant_c.accepted_pairs + variant_c.unresolved_pairs


def test_engine_level_scores_have_no_false_accept_column(tmp_path) -> None:
    manifest_path = build_synthetic_corpus(tmp_path / "corpus")
    manifest = load_manifest(manifest_path)
    root = manifest_path.parent
    primary, secondary = scripted_engines(root, manifest)

    report = run_benchmark(manifest, root, primary)
    # Engine-level reports carry no decision evidence; the field stays absent
    # rather than being invented.
    assert all(case.false_accepted_pairs is None for case in report.cases)
    assert report.aggregate.false_accepted_pairs is None
    assert report.aggregate.mean_cer > 0.0
    assert secondary.name == "paddleocr"
