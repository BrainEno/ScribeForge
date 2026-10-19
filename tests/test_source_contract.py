from __future__ import annotations

import pytest

from scribeforge.domain.source import RenderedPage, page_asset_name


def test_rendered_page_rejects_invalid_evidence() -> None:
    with pytest.raises(ValueError, match="page index"):
        RenderedPage(-1, 10, 10, b"x", "png")
    with pytest.raises(ValueError, match="positive"):
        RenderedPage(0, 0, 10, b"x", "png")
    with pytest.raises(ValueError, match="positive"):
        RenderedPage(0, 10, -5, b"x", "png")
    with pytest.raises(ValueError, match="bytes"):
        RenderedPage(0, 10, 10, b"", "png")
    with pytest.raises(ValueError, match="extension"):
        RenderedPage(0, 10, 10, b"x", "tiff")


def test_rendered_page_carries_immutable_pixel_evidence() -> None:
    page = RenderedPage(3, 1200, 1800, b"\x89PNG", "png")

    assert (page.page_index, page.width, page.height) == (3, 1200, 1800)
    assert page.image_bytes == b"\x89PNG"
    assert page.extension == "png"


def test_page_asset_name_is_zero_padded_and_stable() -> None:
    assert page_asset_name(0, "png") == "page-000000.png"
    assert page_asset_name(12, "jpg") == "page-000012.jpg"

    with pytest.raises(ValueError, match="page index"):
        page_asset_name(-1, "png")
    with pytest.raises(ValueError, match="extension"):
        page_asset_name(0, "bmp")
