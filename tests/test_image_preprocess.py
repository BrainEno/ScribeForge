"""Tests for the scanned-page preprocessing layer.

The fixtures are generated deterministically: synthetic text lines on a white
page, optionally rotated, shadowed or speckled. They are not reproductions of any
book, so they can live in the repository.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from scribeforge.adapters.image_preprocess import (
    PreprocessError,
    apply_preprocess,
    estimate_skew_degrees,
    otsu_threshold,
    preprocess_file,
)
from scribeforge.domain.preprocess import (
    PreprocessOperation,
    PreprocessSettings,
    default_settings,
    passthrough_settings,
    plan_operations,
)


def _text_page(
    *,
    width: int = 600,
    height: int = 800,
    lines: int = 20,
    skew: float = 0.0,
    shadow: bool = False,
    faded: bool = False,
    speckle: bool = False,
) -> Image.Image:
    """Synthetic page of text lines, optionally skewed, shadowed or faded."""

    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    top = 60
    spacing = (height - 120) // lines
    for index in range(lines):
        y = top + index * spacing
        # A row of short strokes stands in for a line of text.
        for column in range(40, width - 40, 14):
            draw.rectangle([column, y, column + 8, y + max(6, spacing // 3)], fill=30)
    if shadow:
        gradient = Image.new("L", (width, height), 255)
        draw_gradient = ImageDraw.Draw(gradient)
        for x in range(width):
            # Dark towards the left edge, as a book spine shadow behaves.
            value = int(255 * (0.45 + 0.55 * min(1.0, x / (width * 0.5))))
            draw_gradient.line([(x, 0), (x, height)], fill=value)
        image = Image.fromarray(
            (_as_array(image) * _as_array(gradient) / 255).astype("uint8"), mode="L"
        )
    if faded:
        # Compress the tonal range as a light impression on old paper does: the
        # strokes never reach black.
        image = image.point(lambda value: int(value * 0.35 + 160))
    if speckle:
        import numpy as np

        rng = np.random.default_rng(20261029)
        array = np.asarray(image).copy()
        mask = rng.random(array.shape) < 0.02
        array[mask] = rng.integers(0, 90, int(mask.sum()), dtype=np.uint8)
        image = Image.fromarray(array, mode="L")
    if skew:
        image = image.rotate(
            skew, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255
        )
    return image


def _as_array(image: Image.Image):
    import numpy as np

    return np.asarray(image, dtype=np.float32)


def _png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- plan


def test_default_settings_do_not_binarize() -> None:
    """Binarization discards greyscale evidence, so it must be opt-in."""

    settings = default_settings()

    assert not settings.enabled(PreprocessOperation.BINARIZE)
    assert settings.enabled(PreprocessOperation.DESKEW)
    assert settings.enabled(PreprocessOperation.DESHADOW)


def test_operations_always_run_in_pipeline_order() -> None:
    """Callers cannot reorder steps by reordering the settings tuple."""

    settings = PreprocessSettings(
        operations=(
            PreprocessOperation.CROP_BORDER,
            PreprocessOperation.BINARIZE,
            PreprocessOperation.DESKEW,
        )
    )

    assert plan_operations(settings) == (
        PreprocessOperation.DESKEW,
        PreprocessOperation.BINARIZE,
        PreprocessOperation.CROP_BORDER,
    )


def test_settings_reject_impossible_parameters() -> None:
    with pytest.raises(ValueError, match="max_skew_degrees"):
        PreprocessSettings(max_skew_degrees=0)
    with pytest.raises(ValueError, match="max_skew_degrees"):
        PreprocessSettings(max_skew_degrees=90)
    with pytest.raises(ValueError, match="denoise_size"):
        PreprocessSettings(denoise_size=4)
    with pytest.raises(ValueError, match="contrast_cutoff"):
        PreprocessSettings(contrast_cutoff=-1)
    with pytest.raises(ValueError, match="border_threshold"):
        PreprocessSettings(border_threshold=256)
    with pytest.raises(ValueError, match="must not repeat"):
        PreprocessSettings(
            operations=(PreprocessOperation.DESKEW, PreprocessOperation.DESKEW)
        )


def test_passthrough_settings_apply_no_operations() -> None:
    assert plan_operations(passthrough_settings()) == ()


def test_fingerprint_is_json_serializable_evidence() -> None:
    import json

    fingerprint = dict(default_settings().fingerprint())

    assert json.loads(json.dumps(fingerprint)) == fingerprint
    assert PreprocessOperation.DESKEW.value in fingerprint["operations"]


# ----------------------------------------------------------------------------- otsu


def test_otsu_splits_a_bimodal_page_near_the_midpoint() -> None:
    import numpy as np

    array = np.concatenate(
        [np.full(500, 25, dtype=np.float32), np.full(500, 235, dtype=np.float32)]
    ).reshape(40, 25)

    threshold = otsu_threshold(array)

    # Otsu's ink class is 0..threshold inclusive, so 25 itself is a valid split.
    assert 25 <= threshold < 235


def test_otsu_on_a_blank_page_still_returns_a_usable_threshold() -> None:
    import numpy as np

    assert 0 <= otsu_threshold(np.full((10, 10), 255, dtype=np.float32)) <= 255


# ---------------------------------------------------------------------------- skew


def test_estimate_skew_recovers_a_known_rotation() -> None:
    """A page rotated by N degrees must be measured as -N (to undo it)."""

    page = _text_page(skew=4.0)

    measured = estimate_skew_degrees(page, max_skew_degrees=8.0)

    assert measured == pytest.approx(-4.0, abs=1.0)


def test_estimate_skew_leaves_an_upright_page_alone() -> None:
    """No rotation on a straight page: inventing one would blur the text."""

    page = _text_page(skew=0.0)

    assert estimate_skew_degrees(page, max_skew_degrees=8.0) == 0.0


def test_estimate_skew_is_bounded_by_the_configured_limit() -> None:
    page = _text_page(skew=6.0)

    measured = estimate_skew_degrees(page, max_skew_degrees=2.0)

    assert abs(measured) <= 2.0


# ---------------------------------------------------------------------- preprocess


def test_deskew_actually_straightens_the_page() -> None:
    """The corrected page must be measurably less skewed than the input."""

    from scribeforge.domain.preprocess import PreprocessOperation as Op

    skewed = _text_page(skew=4.0)
    settings = PreprocessSettings(operations=(Op.DESKEW,))

    data, outcome = apply_preprocess(_png(skewed), settings)

    assert outcome.applied == (Op.DESKEW,)
    assert outcome.measured_skew_degrees == pytest.approx(-4.0, abs=1.0)
    with Image.open(BytesIO(data)) as corrected:
        residual = estimate_skew_degrees(corrected, max_skew_degrees=8.0)
    assert abs(residual) < abs(outcome.measured_skew_degrees)


def test_deshadow_flattens_a_spine_gradient() -> None:
    """The left-to-right illumination spread must shrink noticeably."""

    import numpy as np

    shadowed = _text_page(shadow=True)
    settings = PreprocessSettings(
        operations=(PreprocessOperation.DESHADOW, PreprocessOperation.CONTRAST)
    )

    data, outcome = apply_preprocess(_png(shadowed), settings)

    before = _as_array(shadowed)
    with Image.open(BytesIO(data)) as corrected:
        after = _as_array(corrected)
    # Compare the background column profile, ignoring ink.
    before_background = np.percentile(before, 90, axis=0)
    after_background = np.percentile(after, 90, axis=0)
    assert (before_background.max() - before_background.min()) > 60
    assert (after_background.max() - after_background.min()) < (
        before_background.max() - before_background.min()
    ) / 2
    # Ink on this fixture is already black, so only the shadow is corrected.
    assert outcome.applied == (PreprocessOperation.DESHADOW,)


def test_binarize_produces_a_two_tone_page_when_asked() -> None:
    import numpy as np

    settings = PreprocessSettings(operations=(PreprocessOperation.BINARIZE,))

    data, _ = apply_preprocess(_png(_text_page(shadow=True)), settings)

    with Image.open(BytesIO(data)) as binary:
        values = np.unique(_as_array(binary))
    assert set(values.tolist()) <= {0.0, 255.0}


def test_crop_border_trims_a_wide_margin() -> None:
    wide = Image.new("L", (800, 800), 255)
    draw = ImageDraw.Draw(wide)
    draw.rectangle([300, 300, 500, 500], fill=0)
    settings = PreprocessSettings(operations=(PreprocessOperation.CROP_BORDER,))

    data, outcome = apply_preprocess(_png(wide), settings)

    with Image.open(BytesIO(data)) as cropped:
        assert cropped.size[0] < 800
        assert cropped.size[1] < 800
    assert outcome.notes["crop_border"].startswith("crop=")


def test_crop_border_leaves_a_full_bleed_page_untouched() -> None:
    """Ink touching every edge means there is no margin worth cropping."""

    full_bleed = Image.new("L", (400, 400), 255)
    draw = ImageDraw.Draw(full_bleed)
    # A frame of ink reaching all four edges: cropping would discard content.
    draw.rectangle([0, 0, 399, 399], outline=0, width=20)
    settings = PreprocessSettings(operations=(PreprocessOperation.CROP_BORDER,))

    data, outcome = apply_preprocess(_png(full_bleed), settings)

    assert outcome.notes["crop_border"] == "crop=none"
    with Image.open(BytesIO(data)) as cropped:
        assert cropped.size == (400, 400)


def test_outcome_records_a_hash_of_the_output_bytes() -> None:
    settings = default_settings()

    data, outcome = apply_preprocess(_png(_text_page()), settings)

    import hashlib

    assert outcome.output_sha256 == hashlib.sha256(data).hexdigest()
    assert outcome.page_index == 0


def test_apply_preprocess_rejects_empty_input() -> None:
    with pytest.raises(PreprocessError, match="empty"):
        apply_preprocess(b"", default_settings())


def test_apply_preprocess_wraps_undecodable_input() -> None:
    with pytest.raises(PreprocessError, match="cannot decode"):
        apply_preprocess(b"not an image", default_settings())


def test_preprocess_file_writes_atomically_and_leaves_no_temp(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    source.write_bytes(_png(_text_page()))
    destination = tmp_path / "out" / "page-pre.png"

    outcome = preprocess_file(source, destination, default_settings())

    assert destination.is_file()
    assert not (destination.parent / f".{destination.name}.tmp").exists()
    assert outcome.output_sha256
    # The source raster is evidence and must not be modified.
    with Image.open(source) as original:
        assert original.size == (600, 800)


# --------------------------------------------------------------- degradation gate


def test_clean_page_skips_restoration_it_does_not_need() -> None:
    """A born-digital render must not be blurred and stretched for no reason."""

    data, outcome = apply_preprocess(_png(_text_page()), default_settings())

    assert outcome.applied == ()
    assert "deshadow" in outcome.notes["skipped"]
    assert "contrast" in outcome.notes["skipped"]
    with Image.open(BytesIO(data)) as corrected:
        assert corrected.size == (600, 800)


def test_shadowed_page_gets_deshadow_but_a_faded_one_gets_contrast() -> None:
    shadowed, shadow_outcome = apply_preprocess(
        _png(_text_page(shadow=True)), default_settings()
    )
    faded, faded_outcome = apply_preprocess(
        _png(_text_page(faded=True)), default_settings()
    )

    assert PreprocessOperation.DESHADOW in shadow_outcome.applied
    assert PreprocessOperation.CONTRAST in faded_outcome.applied
    assert shadowed and faded


def test_adapt_to_page_false_runs_every_planned_operation() -> None:
    """Opting out of adaptation restores the blunt, unmeasured pipeline."""

    settings = PreprocessSettings(adapt_to_page=False)

    _, outcome = apply_preprocess(_png(_text_page(shadow=True)), settings)

    assert outcome.applied == (
        PreprocessOperation.DESHADOW,
        PreprocessOperation.CONTRAST,
    )


def test_a_straight_page_is_measured_but_not_rotated() -> None:
    """``applied`` lists transforms that changed pixels, not ones considered."""

    _, outcome = apply_preprocess(_png(_text_page(skew=0.0)), PreprocessSettings())

    assert PreprocessOperation.DESKEW not in outcome.applied
    assert outcome.measured_skew_degrees == 0.0
    assert outcome.notes["deskew"] == "skew=0.0"


def test_explicit_denoise_is_honoured_even_on_a_clean_page() -> None:
    """Denoising is off by default but an operator request is never second-guessed."""

    settings = PreprocessSettings(
        operations=(PreprocessOperation.DENOISE,), denoise_size=3
    )

    _, outcome = apply_preprocess(_png(_text_page()), settings)

    assert outcome.applied == (PreprocessOperation.DENOISE,)


def test_measure_degradation_is_scale_independent_for_ink_darkness() -> None:
    """``dark_point`` must survive a large page, not only a small fixture.

    A downscaled measurement reads anti-aliased stroke edges as grey ink and
    would flag every healthy 300 DPI scan as faded. This pins the behaviour on a
    1800 px wide page, which is downscaled internally before the ink mask.
    """

    from scribeforge.adapters.image_preprocess import measure_degradation

    large = _text_page(width=1800, height=2400, lines=30)
    faded = _text_page(width=1800, height=2400, lines=30, faded=True)

    assert measure_degradation(large).needs_contrast is False
    assert measure_degradation(faded).needs_contrast is True


def test_refine_operations_keeps_geometry_and_operator_extras() -> None:
    from scribeforge.domain.preprocess import PageDegradation, refine_operations

    clean = PageDegradation(
        ink_mean=30.0,
        paper_mean=254.0,
        background_spread=0.0,
        ink_paper_separation=224.0,
        dark_point=5.0,
    )
    settings = PreprocessSettings(
        operations=(
            PreprocessOperation.DESKEW,
            PreprocessOperation.DESHADOW,
            PreprocessOperation.CONTRAST,
            PreprocessOperation.BINARIZE,
            PreprocessOperation.DENOISE,
        )
    )

    refined = refine_operations(settings, clean)

    # Deskew is geometry, binarize/denoise were explicitly requested: all kept.
    assert refined == (
        PreprocessOperation.DESKEW,
        PreprocessOperation.DENOISE,
        PreprocessOperation.BINARIZE,
    )


def test_refine_operations_without_measurements_keeps_the_plan() -> None:
    from scribeforge.domain.preprocess import refine_operations

    settings = default_settings()

    assert refine_operations(settings, None) == plan_operations(settings)
