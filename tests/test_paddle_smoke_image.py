from pathlib import Path

from PIL import Image

from scripts.create_paddle_smoke_image import create_smoke_image


def test_synthetic_smoke_image_is_deterministic_and_platform_portable(
    tmp_path: Path,
) -> None:
    first = create_smoke_image(tmp_path / "first.png")
    second = create_smoke_image(tmp_path / "second.png")

    with Image.open(first) as image:
        assert image.size == (1600, 500)
        assert image.mode == "RGB"
    assert first.read_bytes() == second.read_bytes()
