"""Preprocessing benchmark: does the PREPROCESS stage actually repair damage?

The reference corpus in ``benchmark/corpus/v`` is a 300 DPI scan that measures
clean, so it cannot tell us whether preprocessing works — on those pages the
correct behaviour is to do nothing. This script therefore derives **deterministic
degradations from the real corpus pages** and checks two things per variant:

1. Did the pipeline measure the damage we injected? (skew estimate, shadow and
   fade detection)
2. Did the correction run, and did it move the page back towards the clean
   original? Ink/paper separation and the residual skew are both reported.

Every degradation is seeded, so the numbers are reproducible and the script
needs no OCR backend — only Pillow plus NumPy, both project dependencies.

Usage::

    python benchmark/analysis/preprocess_benchmark.py [--json out.json]
"""

from __future__ import annotations

import argparse
import io
import json
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DIR = REPO_ROOT / "benchmark" / "corpus" / "v"
ANALYSIS_DIR = REPO_ROOT / "benchmark" / "analysis"

SEED = 20261029


def _separability(gray: np.ndarray) -> float:
    """Ink/paper separation in grey levels, using a fixed 128 split.

    A fixed threshold rather than Otsu keeps the number comparable across the
    original and the corrected page; Otsu would move the split and flatter both.
    """

    ink = gray[gray <= 128]
    paper = gray[gray > 128]
    if ink.size == 0 or paper.size == 0:
        return 0.0
    return float(paper.mean() - ink.mean())


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    description: str
    operations: tuple[str, ...] | None = None


VARIANTS = (
    Variant("clean", "unmodified corpus page"),
    Variant("skew-3deg", "rotated 3 degrees as a crooked scan would be"),
    Variant("skew-6deg", "rotated 6 degrees"),
    Variant("spine-shadow", "45% illumination falloff from the left, as a book gutter"),
    Variant("faded", "tonal range compressed to a light impression"),
    Variant("speckled", "2% salt-and-pepper noise, as dirty or damaged paper"),
    Variant(
        "speckled+denoise",
        "same speckle, with the opt-in median filter enabled",
        ("deskew", "deshadow", "contrast", "denoise"),
    ),
    Variant("skew-and-shadow", "both geometry and illumination damage"),
)


def _degrade(image: Image.Image, name: str) -> Image.Image:
    """Apply one deterministic degradation; names may carry a ``+option`` suffix."""

    name = name.split("+", 1)[0]
    gray = image.convert("L")
    if name == "clean":
        return gray

    if name.startswith("skew-") and name.endswith("deg"):
        degrees = float(name.split("-")[1].removesuffix("deg"))
        return gray.rotate(
            degrees, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255
        )

    if name == "spine-shadow":
        width, height = gray.size
        gradient = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(gradient)
        for x in range(width):
            value = int(255 * (0.55 + 0.45 * min(1.0, x / (width * 0.6))))
            draw.line([(x, 0), (x, height)], fill=value)
        product = np.asarray(gray, dtype=np.float32) * np.asarray(
            gradient, dtype=np.float32
        ) / 255.0
        return Image.fromarray(np.clip(product, 0, 255).astype(np.uint8), mode="L")

    if name == "faded":
        return gray.point(lambda value: int(value * 0.35 + 160))

    if name == "speckled":
        rng = np.random.default_rng(SEED)
        array = np.asarray(gray).copy()
        mask = rng.random(array.shape) < 0.02
        array[mask] = rng.integers(0, 90, int(mask.sum()), dtype=np.uint8)
        return Image.fromarray(array, mode="L")

    if name == "skew-and-shadow":
        return _degrade(_degrade(gray, "spine-shadow"), "skew-3deg")

    raise ValueError(f"unknown degradation variant: {name}")


def evaluate_page(image_path: Path, variants: tuple[Variant, ...]) -> list[dict]:
    from scribeforge.adapters.image_preprocess import (
        apply_preprocess,
        estimate_skew_degrees,
        measure_degradation,
    )
    from scribeforge.domain.preprocess import PreprocessOperation, default_settings

    settings = default_settings()
    with Image.open(image_path) as opened:
        original = opened.convert("L")
        original.load()
    clean_sep = _separability(np.asarray(original, dtype=np.float32))

    rows: list[dict] = []
    for variant in variants:
        degraded = _degrade(original, variant.name)
        buffer = io.BytesIO()
        degraded.save(buffer, format="PNG")
        degraded_sep = _separability(np.asarray(degraded, dtype=np.float32))
        measured = measure_degradation(degraded)
        skew_estimate = estimate_skew_degrees(degraded, max_skew_degrees=8.0)

        variant_settings = (
            settings
            if variant.operations is None
            else replace(
                settings,
                operations=tuple(
                    PreprocessOperation(name) for name in variant.operations
                ),
            )
        )
        data, outcome = apply_preprocess(
            buffer.getvalue(), variant_settings, page_index=0
        )
        with Image.open(io.BytesIO(data)) as corrected_image:
            corrected = corrected_image.convert("L")
        corrected_sep = _separability(np.asarray(corrected, dtype=np.float32))
        residual = estimate_skew_degrees(corrected, max_skew_degrees=8.0)

        rows.append(
            {
                "page": image_path.stem,
                "variant": variant.name,
                "description": variant.description,
                "applied": [operation.value for operation in outcome.applied],
                "measured_skew_degrees": round(skew_estimate, 2),
                "residual_skew_degrees": round(residual, 2),
                "separability_clean": round(clean_sep, 1),
                "separability_degraded": round(degraded_sep, 1),
                "separability_corrected": round(corrected_sep, 1),
                "needs_deshadow": measured.needs_deshadow,
                "needs_contrast": measured.needs_contrast,
                "dark_point": round(measured.dark_point, 1),
                "background_spread": round(measured.background_spread, 1),
            }
        )
        degraded.close()
        corrected.close()
    original.close()
    return rows


def summarize(rows: list[dict]) -> dict:
    """Aggregate per-variant behaviour across pages."""

    by_variant: dict[str, list[dict]] = {}
    for row in rows:
        by_variant.setdefault(row["variant"], []).append(row)

    summary: dict[str, dict] = {}
    for variant, group in by_variant.items():
        detected_ops = sorted(
            {operation for row in group for operation in row["applied"]}
        )
        summary[variant] = {
            "pages": len(group),
            "operations_seen": detected_ops,
            "mean_separability_degraded": round(
                sum(row["separability_degraded"] for row in group) / len(group), 1
            ),
            "mean_separability_corrected": round(
                sum(row["separability_corrected"] for row in group) / len(group), 1
            ),
            "max_abs_residual_skew": round(
                max(abs(row["residual_skew_degrees"]) for row in group), 2
            ),
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", type=Path, default=ANALYSIS_DIR / "preprocess_measurements.json"
    )
    args = parser.parse_args(argv)

    images = sorted((CORPUS_DIR / "images").glob("*.png"))
    if not images:
        print(f"no corpus images under {CORPUS_DIR/'images'}; run the corpus extraction first")
        return 2

    rows: list[dict] = []
    for image_path in images:
        rows.extend(evaluate_page(image_path, VARIANTS))

    summary = summarize(rows)
    payload = {
        "schema_version": 1,
        "corpus": str(CORPUS_DIR.relative_to(REPO_ROOT)),
        "variants": [variant.name for variant in VARIANTS],
        "summary": summary,
        "rows": rows,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    header = f"{'variant':<18} {'ops seen':<28} {'sep degraded':>13} {'sep fixed':>10} {'residual skew':>14}"
    print(header)
    print("-" * len(header))
    for variant, stats in summary.items():
        print(
            f"{variant:<18} {','.join(stats['operations_seen']) or '-':<28} "
            f"{stats['mean_separability_degraded']:>13.1f} "
            f"{stats['mean_separability_corrected']:>10.1f} "
            f"{stats['max_abs_residual_skew']:>14.2f}"
        )
    print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
