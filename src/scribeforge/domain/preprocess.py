"""Preprocessing plan for scanned pages.

Old-book and ancient-book scans are routinely skewed, shadowed, faded, speckled
or cropped into the text. Handing such a raster straight to an OCR engine
degrades recognition long before any model quality question is reached, so a
preprocessing stage sits between IMPORT and PRIMARY_OCR.

This module is deliberately dependency-free: it describes *what* to do and
validates the parameters. The pixel work lives in
:mod:`scribeforge.adapters.image_preprocess`. Keeping the plan in the domain
layer means the decisions that change recognized text are unit-testable without
an image library, and the applied parameters can be persisted verbatim as
evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

MAX_SKEW_DEGREES_LIMIT = 45.0


class PreprocessOperation(str, Enum):
    """A single preprocessing transform."""

    DESKEW = "deskew"
    DESHADOW = "deshadow"
    DENOISE = "denoise"
    CONTRAST = "contrast"
    BINARIZE = "binarize"
    CROP_BORDER = "crop_border"


@dataclass(frozen=True, slots=True)
class PreprocessSettings:
    """Which transforms to run and how far they may go.

    Defaults target *degraded but readable* scans: they correct geometry and
    illumination but never threshold the page, because binarization throws away
    the greyscale evidence a human reviewer and the VLM stage rely on. Binarize
    is therefore opt-in.

    Denoising is opt-in as well, on measured evidence rather than intuition. On
    the 300 DPI benchmark scan ``benchmark/corpus/v/images/page_010.png`` a
    median-of-3 filter *lowered* ink/paper separation from 225.8 to 222.6 grey
    levels: at this resolution thin strokes are only a few pixels wide, so the
    filter erodes the very signal the OCR engine resolves. It belongs on genuinely
    speckled or compression-damaged scans, not in the default path.

    ``adapt_to_page`` additionally suppresses restoration steps that a measured
    page does not need (see :func:`refine_operations`), so a clean born-digital
    render is not blurred for no reason.
    """

    operations: tuple[PreprocessOperation, ...] = (
        PreprocessOperation.DESKEW,
        PreprocessOperation.DESHADOW,
        PreprocessOperation.CONTRAST,
    )
    adapt_to_page: bool = True
    max_skew_degrees: float = 8.0
    contrast_cutoff: float = 1.0
    denoise_size: int = 3
    border_threshold: int = 12

    def __post_init__(self) -> None:
        if not 0.0 < self.max_skew_degrees <= MAX_SKEW_DEGREES_LIMIT:
            raise ValueError(
                f"max_skew_degrees must be in (0, {MAX_SKEW_DEGREES_LIMIT}]"
            )
        if not 0.0 <= self.contrast_cutoff < 50.0:
            raise ValueError("contrast_cutoff must be in [0, 50)")
        if self.denoise_size not in (3, 5):
            raise ValueError("denoise_size must be 3 or 5")
        if not 0 <= self.border_threshold <= 255:
            raise ValueError("border_threshold must be in 0..255")
        if len(set(self.operations)) != len(self.operations):
            raise ValueError("preprocess operations must not repeat")

    def enabled(self, operation: PreprocessOperation) -> bool:
        return operation in self.operations

    def fingerprint(self) -> Mapping[str, object]:
        """Stable, JSON-serializable description persisted as evidence."""

        return MappingProxyType(
            {
                "operations": [operation.value for operation in self.operations],
                "max_skew_degrees": self.max_skew_degrees,
                "contrast_cutoff": self.contrast_cutoff,
                "denoise_size": self.denoise_size,
                "border_threshold": self.border_threshold,
                "adapt_to_page": self.adapt_to_page,
            }
        )


def default_settings() -> PreprocessSettings:
    return PreprocessSettings()


def passthrough_settings() -> PreprocessSettings:
    """No transforms at all; used to keep a byte-identical copy as evidence."""

    return PreprocessSettings(operations=())


@dataclass(frozen=True, slots=True)
class PreprocessOutcome:
    """Result of applying a plan to one page.

    ``measured_skew_degrees`` and ``notes`` are the numbers the pipeline acted
    on, not estimates: they are stored so a reviewer can see why a page was
    rotated without re-running anything.
    """

    page_index: int
    settings: PreprocessSettings
    applied: tuple[PreprocessOperation, ...]
    measured_skew_degrees: float = 0.0
    output_sha256: str = ""
    notes: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def as_evidence(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "settings": dict(self.settings.fingerprint()),
                "applied": [operation.value for operation in self.applied],
                "measured_skew_degrees": self.measured_skew_degrees,
                "output_sha256": self.output_sha256,
                "notes": dict(self.notes),
            }
        )


def plan_operations(settings: PreprocessSettings) -> tuple[PreprocessOperation, ...]:
    """Return the transforms in the order they must run.

    Order matters and is fixed here rather than taken from the settings tuple:
    geometry first (deskew), then illumination (deshadow), then noise, then
    tone, then thresholding, and cropping last because it changes the page
    frame that every other step assumes.
    """

    order = (
        PreprocessOperation.DESKEW,
        PreprocessOperation.DESHADOW,
        PreprocessOperation.DENOISE,
        PreprocessOperation.CONTRAST,
        PreprocessOperation.BINARIZE,
        PreprocessOperation.CROP_BORDER,
    )
    return tuple(operation for operation in order if settings.enabled(operation))


# Restoration operations whose usefulness depends on what the page actually
# looks like. Geometry (deskew) and operator-chosen steps are never suppressed.
ADAPTABLE_OPERATIONS: frozenset[PreprocessOperation] = frozenset(
    {PreprocessOperation.DESHADOW, PreprocessOperation.CONTRAST}
)


@dataclass(frozen=True, slots=True)
class PageDegradation:
    """Measured properties of a page raster, used to justify each transform.

    These are the numbers the pipeline decided on, not guesses. They are persisted
    with the plan so a reviewer can see *why* a page was or was not restored.
    """

    ink_mean: float
    paper_mean: float
    background_spread: float
    ink_paper_separation: float
    dark_point: float

    @property
    def needs_deshadow(self) -> bool:
        """Uneven illumination, e.g. a spine shadow or a scanner light falloff."""

        return self.background_spread >= 4.0

    @property
    def needs_contrast(self) -> bool:
        """Faded ink: the stroke cores never reach a dark tone.

        ``dark_point`` must be measured at native resolution. Measured on a
        downscaled copy, the 300 DPI benchmark scans appear to have mid-grey ink
        (mean ~110) purely from anti-aliased edge pixels being averaged in, and a
        naive rule would then "restore" pages that are already perfect. At native
        resolution their ink cores are at 0 — healthy — while a genuinely faded
        page sits above 150.
        """

        return self.dark_point > 60.0

    @property
    def is_degraded(self) -> bool:
        return self.needs_deshadow or self.needs_contrast


def refine_operations(
    settings: PreprocessSettings,
    degradation: PageDegradation | None,
) -> tuple[PreprocessOperation, ...]:
    """Drop restoration steps the measured page does not need.

    A born-digital render has a perfectly uniform background and near-black ink;
    dividing out a background blur and stretching contrast there only adds
    resampling artefacts. Anything the operator asked for beyond the adaptable
    pair is honoured unconditionally, because an explicit request is evidence of
    a known problem with this book.
    """

    planned = plan_operations(settings)
    if degradation is None or not settings.adapt_to_page:
        return planned
    kept: list[PreprocessOperation] = []
    for operation in planned:
        if operation is PreprocessOperation.DESHADOW and not degradation.needs_deshadow:
            continue
        if operation is PreprocessOperation.CONTRAST and not degradation.needs_contrast:
            continue
        kept.append(operation)
    return tuple(kept)
