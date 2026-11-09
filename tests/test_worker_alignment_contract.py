"""Contract-level MinerU + PaddleOCR multiline alignment regressions.

These tests use the workers' actual extraction functions and versioned JSON
result documents, then parse them through the production subprocess adapter.
No OCR model is downloaded or executed and no accuracy claim is made.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine
from scribeforge.domain.alignment import AlignmentStatus, align_pages
from scribeforge.domain.ocr import BlockType, PageOCRResult
from scribeforge.workers import mineru_worker, paddle_worker
from scribeforge.workers.protocol import result_document


def _worker_pages(
    primary_texts: list[str], secondary_texts: list[str], *, paddle_layout: bool
) -> tuple[PageOCRResult, PageOCRResult]:
    primary_content = []
    for index, text in enumerate(primary_texts):
        top = 0.20 + index * 0.04
        primary_content.append(
            {
                "type": "text",
                "content": text,
                "bbox": [0.10, top, 0.90, top + 0.04],
            }
        )
    mineru_raw = {
        "metadata": {"producer": {"name": "mineru", "version": "contract-fixture"}},
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "text",
                        "bbox": [0.10, 0.20, 0.90, 0.20 + len(primary_texts) * 0.04],
                        "content": primary_content,
                    }
                ],
            }
        ],
    }
    paddle_raw: dict[str, object] = {
        "version": "contract-fixture",
        "width": 1000,
        "height": 1000,
        "rec_texts": secondary_texts,
        "rec_scores": [0.99 for _ in secondary_texts],
        "rec_polys": [
            [
                [100, 200 + index * 40],
                [900, 200 + index * 40],
                [900, 240 + index * 40],
                [100, 240 + index * 40],
            ]
            for index in range(len(secondary_texts))
        ],
    }
    if paddle_layout:
        paddle_raw["layout_det_res"] = [
            {
                "label": "text",
                # Pixel corners with page dimensions only at the top level, as
                # opposed to a test-only pre-normalized OCRBlock.
                "bbox": [90, 190, 910, 200 + len(secondary_texts) * 40],
            }
        ]

    primary_lines, primary_blocks = mineru_worker.extract_page(mineru_raw)
    secondary_lines = paddle_worker.extract_lines(paddle_raw)
    secondary_blocks = paddle_worker._layout_blocks(paddle_raw, secondary_lines)
    primary_document = result_document(
        "mineru", "contract-fixture", 0, primary_lines, blocks=primary_blocks
    )
    secondary_document = result_document(
        "paddleocr", "contract-fixture", 0, secondary_lines, blocks=secondary_blocks
    )
    # A real JSON round trip catches values that merely happen to work as Python
    # objects but are not part of the worker wire contract.
    primary_json = json.loads(json.dumps(primary_document, ensure_ascii=False))
    secondary_json = json.loads(json.dumps(secondary_document, ensure_ascii=False))
    primary = SubprocessOCREngine("mineru", ()).parse_document(primary_json, 0)
    secondary = SubprocessOCREngine("paddleocr", ()).parse_document(secondary_json, 0)
    return primary, secondary


@pytest.mark.parametrize(
    ("primary_texts", "secondary_texts", "expected_group", "paddle_layout"),
    [
        (
            ["The quick brown fox"],
            ["The quick", "brown fox"],
            ((0,), (0, 1)),
            True,
        ),
        (
            ["The quick", "brown fox"],
            ["The quick brown fox"],
            ((0, 1), (0,)),
            True,
        ),
        (
            ["The quick brown fox"],
            ["The quick", "brown fox"],
            ((0,), (0, 1)),
            False,
        ),
        (
            ["The quick", "brown fox"],
            ["The quick brown fox"],
            ((0, 1), (0,)),
            False,
        ),
    ],
    ids=["layout-1toN", "layout-Nto1", "plain-default-1toN", "plain-default-Nto1"],
)
def test_default_worker_contract_supports_both_multiline_directions(
    primary_texts: list[str],
    secondary_texts: list[str],
    expected_group: tuple[tuple[int, ...], tuple[int, ...]],
    paddle_layout: bool,
) -> None:
    primary, secondary = _worker_pages(primary_texts, secondary_texts, paddle_layout=paddle_layout)

    result = align_pages(primary, secondary)

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.status is AlignmentStatus.MATCHED
    assert (pair.primary_line_indexes, pair.secondary_line_indexes) == expected_group
    assert pair.conflicts == ()
    assert primary.block_of_line(0) is not None
    if paddle_layout:
        assert secondary.block_of_line(0) is not None
        assert secondary.block_of_line(0).block_type is BlockType.TEXT
        assert [(block.line_start, block.line_end) for block in secondary.blocks] == [
            (0, len(secondary_texts))
        ]
    else:
        assert secondary.blocks == ()


def test_multiline_matching_is_explicitly_allowed_when_both_block_sources_are_unknown() -> None:
    primary, secondary = _worker_pages(
        ["The quick brown fox"], ["The quick", "brown fox"], paddle_layout=False
    )
    primary = replace(primary, blocks=())
    assert primary.blocks == secondary.blocks == ()

    pair = align_pages(primary, secondary).pairs[0]

    assert pair.status is AlignmentStatus.MATCHED
    assert (pair.primary_line_indexes, pair.secondary_line_indexes) == ((0,), (0, 1))


def test_layout_blocks_require_unique_clear_line_ownership() -> None:
    raw = {
        "width": 1000,
        "height": 1000,
        "rec_texts": ["inside", "outside", "overlap"],
        "rec_scores": [0.99, 0.99, 0.99],
        "rec_polys": [
            [[100, 200], [500, 200], [500, 240], [100, 240]],
            [[100, 800], [500, 800], [500, 840], [100, 840]],
            [[500, 500], [900, 500], [900, 540], [500, 540]],
        ],
        "layout_det_res": [
            {"label": "text", "bbox": [90, 190, 510, 250]},
            # The third line is covered by two competing regions; neither may
            # claim it. The second line is outside every region.
            {"label": "text", "bbox": [490, 490, 910, 550]},
            {"label": "title", "bbox": [490, 490, 910, 550]},
        ],
    }
    lines = paddle_worker.extract_lines(raw)

    blocks = paddle_worker._layout_blocks(raw, lines)

    assert [(block["type"], block["line_start"], block["line_end"]) for block in blocks] == [
        ("text", 0, 1)
    ]
    document = result_document("paddleocr", "fixture", 0, lines, blocks=blocks)
    parsed = SubprocessOCREngine("paddleocr", ()).parse_document(document, 0)
    assert parsed.block_of_line(0) is not None
    assert parsed.block_of_line(1) is None
    assert parsed.block_of_line(2) is None


def test_layout_without_lines_emits_no_empty_fake_ranges() -> None:
    raw = {"layout_det_res": [{"label": "title", "bbox": [0.1, 0.1, 0.5, 0.2]}]}
    assert paddle_worker._layout_blocks(raw, []) == []
