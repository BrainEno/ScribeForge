import pytest

from scribeforge.workers.protocol import normalize_polygon, result_document


def test_polygon_is_normalized_to_page_box() -> None:
    box = normalize_polygon([[100, 200], [300, 200], [300, 260], [100, 260]], 1000, 2000)
    assert box == [0.1, 0.1, 0.2, 0.03]


def test_polygon_rejects_invalid_page_dimensions() -> None:
    with pytest.raises(ValueError, match="page dimensions"):
        normalize_polygon([[0, 0], [1, 1]], 0, 10)


def test_result_document_uses_worker_contract_v1() -> None:
    doc = result_document("paddleocr", "3.7.0", 4, [])
    assert doc["schema_version"] == 1
    assert doc["engine"] == "paddleocr"
    assert doc["page_index"] == 4


def test_polygon_clamps_boxes_that_spill_outside_the_page() -> None:
    """Real OCR engines emit block polygons that extend past the page edge.

    A polygon touching or slightly exceeding the page boundary must be clamped
    into the normalized 0..1 page box instead of producing out-of-range geometry
    that later rejects the whole page as invalid evidence.
    """

    box = normalize_polygon([[0, 0], [1200, 0], [1200, 60], [0, 60]], 1000, 800)
    assert box == [0.0, 0.0, 1.0, 0.075]


def test_polygon_clamps_negative_coordinates() -> None:
    """Deskewed/rotated scans can produce polygons with negative coordinates."""

    box = normalize_polygon([[-10, -5], [500, 0], [500, 60], [0, 60]], 1000, 800)
    assert box == [0.0, 0.0, 0.5, 0.075]


def test_polygon_never_exceeds_the_normalized_page() -> None:
    """Any polygon, however large, must normalize into a constructible box."""

    from scribeforge.domain.ocr import BoundingBox

    box = normalize_polygon([[0, 0], [2000, 1600], [2000, 60], [0, 60]], 1000, 800)
    BoundingBox(*box)  # must not raise
    x, y, width, height = box
    assert x >= 0.0 and y >= 0.0
    assert x + width <= 1.0
    assert y + height <= 1.0
