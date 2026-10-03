from pathlib import Path

from PIL import Image

from scribeforge.adapters.image_crops import PillowCropWriter
from scribeforge.domain.crops import PixelBox, ReviewCropPlan


def _source(path: Path) -> None:
    image = Image.new("RGB", (10, 8), "white")
    for x in range(2, 6):
        for y in range(3, 7):
            image.putpixel((x, y), (10, 20, 30))
    image.save(path, format="PNG")


def test_writer_crops_exact_pixel_box_and_returns_pair_paths(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    _source(source)
    output = tmp_path / "crops"
    plan = ReviewCropPlan(0, 4, PixelBox(2, 3, 4, 4), "page-000001-pair-0004.png")

    paths = PillowCropWriter().write(source, output, (plan,))

    assert paths == {4: str(output / plan.file_name)}
    with Image.open(output / plan.file_name) as crop:
        assert crop.size == (4, 4)
        assert crop.getpixel((0, 0)) == (10, 20, 30)
        assert crop.getpixel((3, 3)) == (10, 20, 30)


def test_writer_creates_output_directory_and_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    _source(source)
    output = tmp_path / "nested" / "crops"
    plan = ReviewCropPlan(0, 1, PixelBox(0, 0, 2, 2), "page-000001-pair-0001.png")
    writer = PillowCropWriter()

    first = writer.write(source, output, (plan,))
    second = writer.write(source, output, (plan,))

    assert first == second
    assert (output / plan.file_name).is_file()
    assert list(output.glob("*.tmp")) == []


def test_writer_rejects_crop_outside_source_page(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    _source(source)
    plan = ReviewCropPlan(0, 1, PixelBox(9, 7, 2, 2), "bad.png")

    try:
        PillowCropWriter().write(source, tmp_path / "crops", (plan,))
    except ValueError as exc:
        assert "outside source image" in str(exc)
    else:
        raise AssertionError("out-of-bounds crop should fail")


def test_writer_rejects_duplicate_pair_index_or_file_name(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    _source(source)
    first = ReviewCropPlan(0, 1, PixelBox(0, 0, 2, 2), "a.png")
    duplicate_pair = ReviewCropPlan(0, 1, PixelBox(2, 2, 2, 2), "b.png")
    duplicate_name = ReviewCropPlan(0, 2, PixelBox(2, 2, 2, 2), "a.png")

    for plans in ((first, duplicate_pair), (first, duplicate_name)):
        try:
            PillowCropWriter().write(source, tmp_path / "crops", plans)
        except ValueError as exc:
            assert "duplicate" in str(exc)
        else:
            raise AssertionError("duplicate crop identity should fail")
