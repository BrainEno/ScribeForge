from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.image_crops import materialize_review_crops
from scribeforge.domain.alignment import align_pages
from scribeforge.domain.crops import PixelBox, ReviewCropPlan, plan_review_crops
from scribeforge.domain.ocr import BlockType, BoundingBox, OCRBlock, OCRLine, PageOCRResult
from scribeforge.domain.risk import assess_page, review_candidates


def _page(path: Path, *, size: tuple[int, int] = (8, 6)) -> None:
    image = Image.new("RGB", size)
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel((x, y), (x * 20, y * 30, (x + y) * 10))
    image.save(path, format="PNG")


def test_materializer_writes_exact_planned_pixels_and_returns_pair_paths(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    _page(source)
    output_dir = tmp_path / "review" / "crops"
    plans = (
        ReviewCropPlan(4, 2, PixelBox(2, 1, 3, 2), "page-000005-pair-0002.png"),
        ReviewCropPlan(4, 7, PixelBox(0, 0, 2, 3), "page-000005-pair-0007.png"),
    )

    paths = materialize_review_crops(source, plans, output_dir)

    assert paths == {
        2: str(output_dir / "page-000005-pair-0002.png"),
        7: str(output_dir / "page-000005-pair-0007.png"),
    }
    with Image.open(paths[2]) as crop:
        assert crop.size == (3, 2)
        assert crop.getpixel((0, 0)) == (40, 30, 30)
        assert crop.getpixel((2, 1)) == (80, 60, 60)
    with Image.open(source) as original:
        assert original.size == (8, 6)
        assert original.getpixel((2, 1)) == (40, 30, 30)


def test_materializer_replaces_stale_crop_with_current_source_evidence(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    output = tmp_path / "crops"
    plan = ReviewCropPlan(0, 0, PixelBox(0, 0, 1, 1), "page-000001-pair-0000.png")
    Image.new("RGB", (2, 2), (10, 20, 30)).save(source)

    first = materialize_review_crops(source, (plan,), output)[0]
    with Image.open(first) as crop:
        assert crop.getpixel((0, 0)) == (10, 20, 30)

    Image.new("RGB", (2, 2), (90, 80, 70)).save(source)
    second = materialize_review_crops(source, (plan,), output)[0]

    assert second == first
    with Image.open(second) as crop:
        assert crop.getpixel((0, 0)) == (90, 80, 70)


def test_materializer_rejects_mixed_pages_duplicate_pairs_and_unsafe_names(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    _page(source)
    valid = ReviewCropPlan(1, 2, PixelBox(0, 0, 2, 2), "safe.png")

    with pytest.raises(ValueError, match="same page"):
        materialize_review_crops(
            source,
            (valid, ReviewCropPlan(2, 3, PixelBox(0, 0, 2, 2), "other.png")),
            tmp_path / "out",
        )
    with pytest.raises(ValueError, match="duplicate pair"):
        materialize_review_crops(
            source,
            (valid, ReviewCropPlan(1, 2, PixelBox(2, 2, 2, 2), "other.png")),
            tmp_path / "out",
        )
    with pytest.raises(ValueError, match="file name"):
        materialize_review_crops(
            source,
            (ReviewCropPlan(1, 4, PixelBox(0, 0, 2, 2), "../escape.png"),),
            tmp_path / "out",
        )


def test_materializer_rejects_crop_outside_actual_page_bounds(tmp_path: Path) -> None:
    source = tmp_path / "page.png"
    _page(source, size=(4, 4))
    plan = ReviewCropPlan(0, 1, PixelBox(3, 3, 2, 2), "bad.png")

    with pytest.raises(ValueError, match="bounds"):
        materialize_review_crops(source, (plan,), tmp_path / "out")


def test_materialized_review_crop_uses_line_boxes_not_oversized_block(tmp_path: Path) -> None:
    """A fixture page crop must stay local to the disputed lines."""

    page_width, page_height = 1000, 1600
    source = tmp_path / "fixture-page.png"
    Image.new("RGB", (page_width, page_height), (240, 240, 240)).save(source)
    block_box = BoundingBox(0.05, 0.05, 0.9, 0.9)
    primary_line_box = BoundingBox(0.35, 0.35, 0.10, 0.02)
    secondary_line_box = BoundingBox(0.36, 0.35, 0.10, 0.02)
    primary = PageOCRResult(
        0,
        "mineru",
        "1",
        (OCRLine("乌鸦", primary_line_box),),
        blocks=(OCRBlock(BlockType.TEXT, block_box, 0, 1),),
    )
    secondary = PageOCRResult(
        0,
        "paddleocr",
        "1",
        (OCRLine("鸟鸦", secondary_line_box),),
    )
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary)
    plans = plan_review_crops(
        candidates,
        page_width,
        page_height,
        padding_fraction=0,
    )
    paths = materialize_review_crops(source, plans, tmp_path / "review-crops")
    block_pixel_area = int(block_box.width * page_width) * int(
        block_box.height * page_height
    )

    with Image.open(paths[0]) as crop:
        assert crop.size == (110, 32)
        assert crop.width * crop.height < block_pixel_area * 0.01


def test_empty_plan_does_not_require_source_image(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"

    assert materialize_review_crops(missing, (), tmp_path / "out") == {}
    assert not (tmp_path / "out").exists()


def test_a_scaled_crop_is_enlarged_without_inventing_detail(tmp_path: Path) -> None:
    """Enlargement exists to give the reader more pixels per stroke."""

    source = tmp_path / "page.png"
    _page(source)
    plan = ReviewCropPlan(4, 2, PixelBox(2, 1, 3, 2), "read-02.png", scale=3)

    paths = materialize_review_crops(source, (plan,), tmp_path / "out")

    with Image.open(paths[2]) as crop:
        assert crop.size == (9, 6)
        # Nearest-neighbour: the enlarged pixel is the scanned pixel, not a blur.
        assert crop.getpixel((0, 0)) == (40, 30, 30)
        assert crop.getpixel((1, 1)) == (40, 30, 30)
        assert crop.getpixel((3, 0)) == (60, 30, 40)
