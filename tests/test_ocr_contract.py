import pytest

from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult, page_text, tokens


def test_bounding_box_rejects_coordinates_outside_page() -> None:
    with pytest.raises(ValueError):
        BoundingBox(x=0.9, y=0.0, width=0.2, height=0.1)


def test_token_rejects_invalid_confidence() -> None:
    with pytest.raises(ValueError):
        OCRToken("字", 1.1, BoundingBox(0.0, 0.0, 0.1, 0.1))


def test_page_result_preserves_line_order_and_evidence() -> None:
    first = OCRToken("乌", 0.82, BoundingBox(0.1, 0.1, 0.05, 0.05))
    second = OCRToken("鸦", 0.91, BoundingBox(0.15, 0.1, 0.05, 0.05))
    result = PageOCRResult(
        page_index=0,
        engine="fake",
        engine_version="1",
        lines=(OCRLine("乌鸦", BoundingBox(0.1, 0.1, 0.1, 0.05), (first, second)),),
    )

    assert page_text(result) == "乌鸦"
    assert tuple(tokens(result)) == (first, second)


def test_page_index_is_zero_based() -> None:
    with pytest.raises(ValueError):
        PageOCRResult(-1, "fake", "1", ())
