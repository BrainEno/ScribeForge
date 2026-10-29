import pytest

from scribeforge.workers.protocol import normalize_polygon, result_document


def test_polygon_is_normalized_to_page_box() -> None:
    box = normalize_polygon([[100, 200], [300, 200], [300, 260], [100, 260]], 1000, 2000)
    assert box == [0.1, 0.1, 0.2, 0.03]


def test_polygon_rejects_invalid_page_dimensions() -> None:
    with pytest.raises(ValueError, match="page dimensions"):
        normalize_polygon([[0, 0], [1, 1]], 0, 10)


def test_result_document_defaults_to_worker_contract_v2() -> None:
    """New workers emit v2; v1 remains available explicitly for older workers."""

    doc = result_document("paddleocr", "3.7.0", 4, [])
    assert doc["schema_version"] == 2
    assert doc["engine"] == "paddleocr"
    assert doc["page_index"] == 4
    assert doc["blocks"] == []


def test_result_document_omits_a_direction_the_worker_cannot_vouch_for() -> None:
    """Silence is not a horizontal declaration.

    If a worker emitted a constant ``horizontal-lr``, the adapter would record it
    as an engine assertion and the geometry-based detection would never run —
    every page of a vertical book would be paired along the wrong axis.
    """

    doc = result_document("mineru", "4.1.0", 0, [])

    assert "direction" not in doc


def test_reported_direction_only_accepts_real_values() -> None:
    from scribeforge.workers.protocol import reported_direction

    assert reported_direction("VERTICAL-RL") == "vertical-rl"
    assert reported_direction(" horizontal-lr ") == "horizontal-lr"
    assert reported_direction("sideways") is None
    assert reported_direction(None) is None
    assert reported_direction(7) is None


def test_result_document_rejects_an_unknown_direction() -> None:
    with pytest.raises(ValueError, match="unknown page direction"):
        result_document("mineru", "4.1.0", 0, [], direction="sideways")


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


def test_result_document_v2_carries_blocks_and_direction() -> None:
    """Contract v2 reports structural blocks and reading direction."""

    from scribeforge.workers.protocol import block_document

    doc = result_document(
        "mineru",
        "4.1.0",
        2,
        [{"text": "第 一 章", "box": [0.1, 0.1, 0.5, 0.05], "tokens": []}],
        blocks=[block_document("title", [0.1, 0.1, 0.5, 0.05], 0, 1)],
        direction="vertical-rl",
    )

    assert doc["schema_version"] == 2
    assert doc["direction"] == "vertical-rl"
    assert doc["blocks"] == [
        {"type": "title", "box": [0.1, 0.1, 0.5, 0.05], "line_start": 0, "line_end": 1}
    ]


def test_result_document_v1_stays_supported() -> None:
    """A worker still on v1 must keep producing a parseable document."""

    doc = result_document("mineru", "4.0.0", 0, [], schema_version=1)

    assert doc["schema_version"] == 1
    assert "blocks" not in doc
    assert "direction" not in doc


def test_result_document_rejects_an_unknown_schema_version() -> None:
    with pytest.raises(ValueError, match="unsupported worker schema"):
        result_document("mineru", "4.0.0", 0, [], schema_version=99)


def test_canonical_block_label_maps_engine_aliases() -> None:
    """Engine-specific labels collapse onto the canonical set."""

    from scribeforge.workers.protocol import canonical_block_label

    assert canonical_block_label("page_footnote") == "footnote"
    assert canonical_block_label("image") == "figure"
    assert canonical_block_label("page_header") == "header"
    assert canonical_block_label("paragraph") == "text"
    # An unknown label must not fail the page.
    assert canonical_block_label("brand_new_engine_label") == "unknown"
    assert canonical_block_label(None) == "unknown"


def test_block_document_rejects_a_reversed_line_range() -> None:
    from scribeforge.workers.protocol import block_document

    with pytest.raises(ValueError, match="forward range"):
        block_document("text", [0.0, 0.0, 0.5, 0.5], 3, 2)


def test_clamp_box_keeps_a_box_inside_the_page() -> None:
    from scribeforge.workers.protocol import clamp_box

    assert clamp_box([0.9, 0.9, 0.5, 0.5]) == pytest.approx([0.9, 0.9, 0.1, 0.1])
    assert clamp_box([-0.1, -0.1, 0.5, 0.5]) == pytest.approx([0.0, 0.0, 0.5, 0.5])
