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
