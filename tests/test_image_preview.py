from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.image_preview import load_preview


def _write_image(path: Path, size: tuple[int, int] = (200, 100)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


def test_load_preview_shrinks_to_fit_the_box(tmp_path: Path) -> None:
    preview = load_preview(_write_image(tmp_path / "page.png"), box=(50, 50))

    assert preview.size == (50, 25)
    assert preview.mode == "RGB"


def test_load_preview_keeps_a_small_image_at_native_size(tmp_path: Path) -> None:
    preview = load_preview(_write_image(tmp_path / "page.png", (20, 10)), box=(80, 80))

    assert preview.size == (20, 10)


def test_load_preview_can_enlarge_a_crop(tmp_path: Path) -> None:
    preview = load_preview(
        _write_image(tmp_path / "crop.png", (20, 10)), box=(80, 80), upscale=True
    )

    assert preview.size == (80, 40)


def test_load_preview_converts_a_bitonal_scan(tmp_path: Path) -> None:
    path = tmp_path / "scan.png"
    Image.new("1", (30, 30), 1).save(path)

    assert load_preview(path, box=(10, 10)).mode == "RGB"


def test_load_preview_applies_the_exif_orientation(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    image = Image.new("RGB", (40, 20), "white")
    exif = image.getexif()
    exif[274] = 6
    image.save(path, exif=exif)

    assert load_preview(path, box=(100, 100)).size == (20, 40)


def test_load_preview_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_preview(tmp_path / "absent.png", box=(10, 10))


def test_load_preview_rejects_undecodable_bytes(tmp_path: Path) -> None:
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")

    with pytest.raises(ValueError, match="cannot read image"):
        load_preview(path, box=(10, 10))
