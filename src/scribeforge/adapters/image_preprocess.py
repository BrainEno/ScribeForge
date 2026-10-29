"""Pixel-level preprocessing for scanned pages.

Implements the plan from :mod:`scribeforge.domain.preprocess` with Pillow and
NumPy only. Every transform here is a classical, explainable algorithm rather
than a model call: the point is to hand the OCR engines an image whose geometry
and illumination are sane, not to guess at characters.

Nothing in this module is allowed to run on the original evidence. The caller
writes the output beside the untouched raster and records both.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from scribeforge.domain.preprocess import (
    PageDegradation,
    PreprocessOperation,
    PreprocessOutcome,
    PreprocessSettings,
    refine_operations,
)

_MAX_ANALYSIS_EDGE = 900
_SKEW_STEP_DEGREES = 0.5
# A page whose best projection score barely beats the upright score is not
# skewed; rotating it would invent a correction and blur the text.
_MIN_SKEW_GAIN = 1.02


class PreprocessError(RuntimeError):
    """The raster could not be preprocessed."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _to_gray_array(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float32)


def otsu_threshold(gray: np.ndarray) -> int:
    """Otsu's method on an 8-bit grayscale array.

    Returns a threshold that maximizes between-class variance, with Otsu's
    convention that the ink class is ``0..threshold`` inclusive. Callers must
    therefore test ``value <= threshold`` for ink; using ``<`` would classify the
    darkest ink level as background on a clean two-tone page.

    Used both for the deskew projection profile and for opt-in binarization.
    """

    values = np.clip(gray, 0, 255).astype(np.uint8)
    histogram = np.bincount(values.ravel(), minlength=256).astype(np.float64)
    total = histogram.sum()
    if total == 0:
        return 127

    levels = np.arange(256, dtype=np.float64)
    weight_background = np.cumsum(histogram)
    weight_foreground = total - weight_background
    # Avoid dividing by zero on empty classes.
    valid = (weight_background > 0) & (weight_foreground > 0)
    if not valid.any():
        return 127

    cumulative_mean = np.cumsum(histogram * levels)
    total_mean = cumulative_mean[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        mean_background = np.where(
            weight_background > 0, cumulative_mean / weight_background, 0.0
        )
        mean_foreground = np.where(
            weight_foreground > 0,
            (total_mean - cumulative_mean) / weight_foreground,
            0.0,
        )
        between = (
            weight_background
            * weight_foreground
            * (mean_background - mean_foreground) ** 2
        )
    between = np.where(valid, between, -1.0)
    return int(np.argmax(between))


def threshold_mask(gray: np.ndarray) -> np.ndarray:
    """Otsu ink mask, quantized exactly as the threshold was computed.

    ``otsu_threshold`` builds its histogram on ``uint8`` values while callers hold
    float arrays. Comparing a float array against the integer cut is therefore
    wrong at the boundary: a flattened ink tone of 174.096 against a threshold of
    174 produced an *empty* mask, which silently disabled the whole estimator.
    Quantizing first makes the two agree.
    """

    quantized = np.clip(gray, 0, 255).astype(np.uint8)
    mask: np.ndarray = quantized <= otsu_threshold(quantized.astype(np.float32))
    return mask


def _projection_score(binary: np.ndarray) -> float:
    """Normalized variance of the horizontal ink profile.

    Straight text lines produce sharp rows of ink separated by gaps, so the
    profile variance peaks when the page is upright. This is the classic
    projection-profile deskew objective, with one essential correction: the
    variance is divided by the squared mean ink per row.

    Without that normalization the estimator is systematically biased towards
    rotating, because bilinear resampling smears stroke edges and pushes extra
    pixels below the threshold. Measured on the born-digital benchmark pages
    (which cannot be skewed at all) the raw variance rose ~5% at ±0.5 degrees,
    which is exactly the kind of invented correction that would soften every
    glyph on a straight page. The normalized score is flat-topped at zero there
    and still peaks sharply on genuinely skewed scans.
    """

    profile = binary.sum(axis=1, dtype=np.float64)
    if profile.size == 0:
        return 0.0
    mean = profile.mean()
    if mean <= 0:
        return 0.0
    return float(profile.var() / (mean * mean))


def measure_degradation(image: Image.Image) -> PageDegradation:
    """Measure how much restoration a page actually needs.

    Three cheap, explainable quantities on a downscaled copy:

    - ``ink_paper_separation``: mean paper tone minus mean ink tone. Faded scans
      compress this.
    - ``ink_mean``: how dark the strokes reach. Grey ink means a low-contrast
      original.
    - ``background_spread``: the p95-p5 range of per-column background tone.
      A spine shadow or light falloff shows up here; a clean render sits at zero.
    - ``dark_point``: the 5th percentile of ink pixels **at native resolution**.
      Downscaling averages anti-aliased stroke edges into the ink mask and makes
      every good scan look faded, so this one statistic is measured on the full
      raster. It costs about 0.15 s on a 9 megapixel page.
    """

    gray_full = _to_gray_array(image)
    analysis = _analysis_image(image)
    gray = _to_gray_array(analysis)
    flattened = flatten_illumination(gray)
    mask = threshold_mask(flattened)
    ink = flattened[mask]
    paper = flattened[~mask]
    if ink.size == 0 or paper.size == 0:
        # A blank or saturated page: report neutral values so nothing is applied
        # that would be justified by a meaningless statistic.
        return PageDegradation(
            ink_mean=0.0,
            paper_mean=255.0,
            background_spread=0.0,
            ink_paper_separation=255.0,
            dark_point=0.0,
        )
    # Measured on the raw (not flattened) page: flattening removes exactly the
    # uneven illumination this statistic exists to detect.
    per_column = np.percentile(gray, 90, axis=0)
    spread = float(np.percentile(per_column, 95) - np.percentile(per_column, 5))
    full_mask = threshold_mask(flatten_illumination(gray_full))
    full_ink = gray_full[full_mask]
    dark_point = float(np.percentile(full_ink, 5)) if full_ink.size else 0.0
    return PageDegradation(
        ink_mean=float(ink.mean()),
        paper_mean=float(paper.mean()),
        background_spread=max(0.0, spread),
        ink_paper_separation=float(paper.mean() - ink.mean()),
        dark_point=dark_point,
    )


def flatten_illumination(gray: np.ndarray) -> np.ndarray:
    """Remove column-wise illumination before measuring or binarizing.

    A spine shadow darkens one side of the page. With a single global threshold
    that side loses ink while the lit side gains it, which biases the projection
    profile and made the deskew estimator rotate a perfectly straight page by the
    full search limit. Dividing each column by its own background level (the 95th
    percentile of that column, i.e. its paper tone) equalizes the two sides before
    any threshold is applied.

    Returns a float array in 0..255 that keeps each column's tonal range.
    """

    if gray.ndim != 2 or gray.size == 0:
        return gray
    background = np.percentile(gray, 95, axis=0)
    background = np.maximum(background, 1.0)
    flattened: np.ndarray = np.clip(gray / background * 255.0, 0, 255)
    return flattened


def _analysis_image(image: Image.Image) -> Image.Image:
    scaled = image.convert("L")
    width, height = scaled.size
    longest = max(width, height)
    if longest <= _MAX_ANALYSIS_EDGE:
        return scaled
    ratio = _MAX_ANALYSIS_EDGE / longest
    return scaled.resize(
        (max(1, int(width * ratio)), max(1, int(height * ratio))),
        Image.Resampling.BILINEAR,
    )


def estimate_skew_degrees(
    image: Image.Image, *, max_skew_degrees: float, step: float = _SKEW_STEP_DEGREES
) -> float:
    """Estimate page skew in degrees (positive = counter-clockwise).

    Returns ``0.0`` when the page is not measurably skewed, so an upright page is
    never rotated on noise.
    """

    if max_skew_degrees <= 0:
        return 0.0
    analysis = _analysis_image(image)
    binary = threshold_mask(flatten_illumination(_to_gray_array(analysis)))
    upright = _projection_score(binary)

    best_angle = 0.0
    best_score = upright
    steps = round(max_skew_degrees / step)
    for index in range(1, steps + 1):
        for sign in (1.0, -1.0):
            angle = sign * index * step
            rotated_gray = _to_gray_array(
                analysis.rotate(
                    angle, resample=Image.Resampling.BILINEAR, fillcolor=255
                )
            )
            score = _projection_score(
                threshold_mask(flatten_illumination(rotated_gray))
            )
            if score > best_score:
                best_score = score
                best_angle = angle

    if upright <= 0:
        return best_angle
    if best_score < upright * _MIN_SKEW_GAIN:
        return 0.0
    return best_angle


def _deskew(
    image: Image.Image, measured: float
) -> tuple[Image.Image, dict[str, str]]:
    if measured == 0.0:
        return image, {"skew": "0.0"}
    corrected = image.rotate(
        measured,
        resample=Image.Resampling.BICUBIC,
        expand=False,
        fillcolor=_page_fill(image),
    )
    return corrected, {"skew": f"{measured:.2f}"}


def _page_fill(image: Image.Image) -> tuple[int, ...]:
    """Fill colour for exposed corners: the page's own modal background tone."""

    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    if gray.size == 0:
        return (255, 255, 255)
    histogram = np.bincount(gray.ravel(), minlength=256)
    background = int(np.argmax(histogram))
    if image.mode in {"RGB", "RGBA"}:
        return (background, background, background)
    return (background,)


def _deshadow(image: Image.Image) -> tuple[Image.Image, dict[str, str]]:
    """Remove low-frequency illumination by dividing out a blurred background.

    A book spine casts a soft gradient that Otsu alone cannot fix; dividing by a
    heavily blurred copy flattens it while keeping stroke contrast.
    """

    gray = image.convert("L")
    width, height = gray.size
    radius = max(8, max(width, height) // 20)
    background = gray.filter(ImageFilter.GaussianBlur(radius=radius))
    background_array = np.asarray(background, dtype=np.float32)
    background_array = np.maximum(background_array, 1.0)
    normalized = np.clip(
        np.asarray(gray, dtype=np.float32) / background_array * 255.0, 0, 255
    ).astype(np.uint8)
    return Image.fromarray(normalized, mode="L"), {"background_radius": str(radius)}


def _denoise(image: Image.Image, size: int) -> tuple[Image.Image, dict[str, str]]:
    return image.filter(ImageFilter.MedianFilter(size=size)), {"median_size": str(size)}


def _contrast(image: Image.Image, cutoff: float) -> tuple[Image.Image, dict[str, str]]:
    return ImageOps.autocontrast(image.convert("L"), cutoff=cutoff), {
        "cutoff": f"{cutoff:g}"
    }


def _binarize(image: Image.Image) -> tuple[Image.Image, dict[str, str]]:
    gray = image.convert("L")
    flattened = flatten_illumination(_to_gray_array(gray))
    ink = threshold_mask(flattened)
    binary = np.where(ink, 0, 255).astype(np.uint8)
    return Image.fromarray(binary, mode="L"), {
        "otsu_threshold": str(otsu_threshold(flattened))
    }


def _crop_border(
    image: Image.Image, threshold: int
) -> tuple[Image.Image, dict[str, str]]:
    """Trim uniform margins, keeping a small safety border.

    Scanners often capture a wide grey gutter or desk edge. Cropping it stops
    the engines from wasting detection on it, but the crop must stay conservative
    so page-furniture detection still sees the real page frame.
    """

    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    if gray.size == 0:
        return image, {"crop": "none"}
    background = int(np.argmax(np.bincount(gray.ravel(), minlength=256)))
    ink = np.abs(gray.astype(np.int16) - background) > threshold
    if not ink.any():
        return image, {"crop": "none"}
    rows = np.flatnonzero(ink.any(axis=1))
    columns = np.flatnonzero(ink.any(axis=0))
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    left, right = int(columns[0]), int(columns[-1]) + 1

    margin = max(4, int(min(gray.shape) * 0.01))
    top = max(0, top - margin)
    left = max(0, left - margin)
    bottom = min(gray.shape[0], bottom + margin)
    right = min(gray.shape[1], right + margin)
    if (left, top, right, bottom) == (0, 0, gray.shape[1], gray.shape[0]):
        return image, {"crop": "none"}
    return image.crop((left, top, right, bottom)), {
        "crop": f"{left},{top},{right},{bottom}"
    }


def apply_preprocess(
    image_bytes: bytes,
    settings: PreprocessSettings,
    *,
    page_index: int = 0,
) -> tuple[bytes, PreprocessOutcome]:
    """Apply ``settings`` to an encoded page and return PNG bytes plus evidence.

    The output is always PNG so downstream stages see a lossless raster; JPEG
    re-encoding would add compression artefacts to the very strokes the OCR
    engine has to resolve.
    """

    if not image_bytes:
        raise PreprocessError("preprocess input image is empty")
    try:
        with Image.open(BytesIO(image_bytes)) as opened:
            image = opened.convert("L")
            image.load()
    except Exception as exc:
        raise PreprocessError(f"cannot decode page raster: {exc}") from exc

    degradation = measure_degradation(image)
    operations = refine_operations(settings, degradation)
    planned = settings.operations
    skipped = [
        operation.value for operation in planned if operation not in operations
    ]
    applied: list[PreprocessOperation] = []
    notes: dict[str, str] = {
        "measured": (
            f"ink_mean={degradation.ink_mean:.1f};"
            f"paper_mean={degradation.paper_mean:.1f};"
            f"background_spread={degradation.background_spread:.1f};"
            f"separation={degradation.ink_paper_separation:.1f};"
            f"dark_point={degradation.dark_point:.1f}"
        )
    }
    if skipped:
        notes["skipped"] = (
            "page is clean enough to skip restoration: " + ",".join(skipped)
        )
    measured_skew = 0.0

    try:
        for operation in operations:
            if operation is PreprocessOperation.DESKEW:
                measured_skew = estimate_skew_degrees(
                    image, max_skew_degrees=settings.max_skew_degrees
                )
                image, detail = _deskew(image, measured_skew)
                if measured_skew == 0.0:
                    # The page was measured and found straight. Record the
                    # measurement, but do not claim a transform that did not
                    # happen: ``applied`` is evidence, not intent.
                    applied_skew_note = "; ".join(
                        f"{key}={value}" for key, value in detail.items()
                    )
                    notes[operation.value] = applied_skew_note
                    continue
            elif operation is PreprocessOperation.DESHADOW:
                image, detail = _deshadow(image)
            elif operation is PreprocessOperation.DENOISE:
                image, detail = _denoise(image, settings.denoise_size)
            elif operation is PreprocessOperation.CONTRAST:
                image, detail = _contrast(image, settings.contrast_cutoff)
            elif operation is PreprocessOperation.BINARIZE:
                image, detail = _binarize(image)
            elif operation is PreprocessOperation.CROP_BORDER:
                image, detail = _crop_border(image, settings.border_threshold)
            else:  # pragma: no cover - guarded by the enum
                raise PreprocessError(f"unsupported preprocess operation: {operation}")
            applied.append(operation)
            notes[operation.value] = "; ".join(
                f"{key}={value}" for key, value in detail.items()
            )

        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=False, compress_level=6)
        data = buffer.getvalue()
    except PreprocessError:
        raise
    except Exception as exc:
        raise PreprocessError(f"preprocessing failed: {exc}") from exc

    outcome = PreprocessOutcome(
        page_index=page_index,
        settings=settings,
        applied=tuple(applied),
        measured_skew_degrees=measured_skew,
        output_sha256=_sha256(data),
        notes=notes,
    )
    return data, outcome


def preprocess_file(
    source: Path,
    destination: Path,
    settings: PreprocessSettings,
    *,
    page_index: int = 0,
) -> PreprocessOutcome:
    """Preprocess ``source`` into ``destination`` atomically.

    The destination is replaced via a temporary file so an interrupted run never
    leaves a half-written raster that later looks like valid evidence.
    """

    data, outcome = apply_preprocess(
        Path(source).read_bytes(), settings, page_index=page_index
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return outcome
