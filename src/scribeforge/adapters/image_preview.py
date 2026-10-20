"""Display-only image previews for the desktop shell.

This is the image I/O boundary of the viewer. Scaling, mode conversion, and EXIF
orientation only affect what a reviewer sees on screen: previews are never
persisted, never written back, and never feed the OCR, verification, decision, or
export layers.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

_RESAMPLE = Image.Resampling.LANCZOS


def load_preview(
    path: Path, *, box: tuple[int, int], upscale: bool = False
) -> Image.Image:
    """Open an image as an RGB preview fitted into ``box``.

    ``upscale`` enlarges small crops so the reviewer can read them; without it a
    preview is only ever shrunk. A missing file raises `FileNotFoundError` and
    undecodable bytes raise `ValueError`, so the shell can report the problem
    instead of crashing.
    """

    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with Image.open(path) as image:
            oriented = ImageOps.exif_transpose(image) or image
            rgb = oriented.convert("RGB")
    except UnidentifiedImageError as exc:
        raise ValueError(f"cannot read image: {path}") from exc
    return _fit(rgb, box, upscale=upscale)


def _fit(image: Image.Image, box: tuple[int, int], *, upscale: bool) -> Image.Image:
    width, height = image.size
    factor = min(box[0] / width, box[1] / height)
    if factor >= 1 and not upscale:
        return image
    size = (max(1, round(width * factor)), max(1, round(height * factor)))
    return image.resize(size, _RESAMPLE)
