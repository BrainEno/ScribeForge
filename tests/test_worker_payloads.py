import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribeforge.workers import mineru_worker, paddle_worker


def test_mineru_extract_lines_reads_middle_json_blocks() -> None:
    raw = {
        "metadata": {"producer": {"name": "mineru", "version": "4.0.12"}},
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "text",
                        "bbox": [0.1, 0.2, 0.4, 0.25],
                        "content": [{"type": "text", "content": "第一行"}],
                    },
                    {
                        "type": "page_footnote",
                        "bbox": [0.2, 0.8, 0.5, 0.85],
                        "content": [
                            {"type": "text", "content": "① 脚注"},
                            {"type": "text", "content": "续行"},
                        ],
                    },
                ],
            }
        ],
    }

    lines = mineru_worker.extract_lines(raw)

    assert [line["text"] for line in lines] == ["第一行", "① 脚注", "续行"]
    assert lines[0]["box"][:2] == [0.1, 0.2]
    assert lines[0]["box"][2] == pytest.approx(0.3)
    assert lines[0]["box"][3] == pytest.approx(0.05)
    assert lines[1]["box"][:2] == [0.2, 0.8]
    assert lines[1]["box"][2] == pytest.approx(0.3)


def test_mineru_extract_lines_rejects_bad_payloads() -> None:
    with pytest.raises(TypeError):
        mineru_worker.extract_lines([])
    with pytest.raises(ValueError, match="pages"):
        mineru_worker.extract_lines({"pages": []})
    with pytest.raises(TypeError, match="content"):
        mineru_worker.extract_lines(
            {"pages": [{"blocks": [{"bbox": [0, 0, 1, 1], "content": "bad"}]}]}
        )
    assert (
        mineru_worker.extract_lines(
            {"pages": [{"blocks": [{"bbox": [0, 0, 1, 1], "content": []}]}]}
        )
        == []
    )
    with pytest.raises(ValueError, match="bbox"):
        mineru_worker.extract_lines(
            {
                "pages": [
                    {
                        "blocks": [
                            {
                                "bbox": [0, 0, 1],
                                "content": [{"type": "text", "content": "x"}],
                            }
                        ]
                    }
                ]
            }
        )


def test_mineru_main_emits_worker_contract(monkeypatch, capsys, tmp_path: Path) -> None:
    raw = {
        "metadata": {"producer": {"name": "mineru", "version": "4.0.12"}},
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "text",
                        "bbox": [0.1, 0.2, 0.4, 0.25],
                        "content": [{"type": "text", "content": "乌鸦"}],
                    }
                ],
            }
        ],
    }

    run_kwargs = {}

    def fake_run(argv, **kwargs):
        run_kwargs.update(kwargs)
        output = Path(argv[argv.index("-o") + 1])
        (output / "page.json").write_text(json.dumps(raw), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    image = tmp_path / "page.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(mineru_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["mineru-worker", "--image", str(image), "--page-index", "3"])

    mineru_worker.main()

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["engine"] == "mineru"
    assert emitted["engine_version"] == "4.0.12"
    assert emitted["page_index"] == 3
    assert emitted["lines"][0]["text"] == "乌鸦"
    assert run_kwargs["stdout"] is sys.stderr
    assert run_kwargs["stderr"] is sys.stderr
    assert "capture_output" not in run_kwargs


def test_paddle_extract_lines_maps_confidence_and_geometry() -> None:
    raw = [
        {
            "width": 1000,
            "height": 2000,
            "rec_texts": ["乌鸦"],
            "rec_scores": [0.97],
            "rec_polys": [[[100, 200], [300, 200], [300, 260], [100, 260]]],
        }
    ]

    lines = paddle_worker.extract_lines(raw)

    assert lines[0]["text"] == "乌鸦"
    assert lines[0]["tokens"][0]["confidence"] == 0.97
    assert lines[0]["box"] == [0.1, 0.1, 0.2, 0.03]


def test_paddle_extract_lines_rejects_invalid_arrays() -> None:
    with pytest.raises(TypeError):
        paddle_worker.extract_lines("bad")
    with pytest.raises(TypeError, match="must be lists"):
        paddle_worker.extract_lines({"rec_texts": None, "rec_scores": [], "rec_polys": []})
    with pytest.raises(ValueError, match="inconsistent lengths"):
        paddle_worker.extract_lines({"rec_texts": ["x"], "rec_scores": [], "rec_polys": []})
    with pytest.raises(TypeError, match="polygon"):
        paddle_worker.extract_lines({"rec_texts": ["x"], "rec_scores": [0.9], "rec_polys": ["bad"]})


def test_paddle_extract_lines_uses_image_size_when_result_omits_it() -> None:
    raw = {
        "rec_texts": ["问"],
        "rec_scores": [0.91],
        "rec_polys": [[[10, 20], [20, 20], [20, 30], [10, 30]]],
    }

    lines = paddle_worker.extract_lines(raw, image_size=(100, 200))

    assert lines[0]["box"] == [0.1, 0.1, 0.1, 0.05]


def test_paddle_extract_lines_rejects_unknown_page_size() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        paddle_worker.extract_lines(
            {
                "rec_texts": ["问"],
                "rec_scores": [0.9],
                "rec_polys": [[[0, 0], [1, 0], [1, 1], [0, 1]]],
            }
        )


def test_paddle_main_writes_contract_from_saved_result(monkeypatch, capsys, tmp_path: Path) -> None:
    raw = {
        "version": "3.5.0",
        "rec_texts": ["问"],
        "rec_scores": [0.91],
        "rec_polys": [[[10, 20], [20, 20], [20, 30], [10, 30]]],
    }

    def fake_run(argv, **kwargs):
        output = Path(argv[argv.index("--save_path") + 1])
        (output / "page_res.json").write_text(json.dumps(raw), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    image = tmp_path / "page.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(paddle_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(paddle_worker, "_image_size", lambda path: (100, 200))
    monkeypatch.setattr(sys, "argv", ["paddle-worker", "--image", str(image), "--page-index", "5"])

    paddle_worker.main()

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["engine"] == "paddleocr"
    assert emitted["engine_version"] == "3.5.0"
    assert emitted["page_index"] == 5
    assert emitted["lines"][0]["text"] == "问"
    assert emitted["lines"][0]["box"] == [0.1, 0.1, 0.1, 0.05]


def test_mineru_extract_lines_clamps_block_boxes_to_the_page() -> None:
    """MinerU block bboxes can touch or spill past the page edge.

    Normalized middle-JSON geometry that exceeds 0..1 or uses a box taller than
    the remaining page must clamp into a constructible normalized box rather
    than failing the page.
    """

    from scribeforge.domain.ocr import BoundingBox

    raw = {
        "pages": [
            {
                "blocks": [
                    {
                        "type": "text",
                        "bbox": [0.9, 0.95, 1.02, 1.01],
                        "content": [{"type": "text", "content": "边界"}],
                    },
                    {
                        "type": "text",
                        "bbox": [-0.01, -0.02, 0.5, 0.1],
                        "content": [{"type": "text", "content": "左上"}],
                    },
                ]
            }
        ]
    }

    lines = mineru_worker.extract_lines(raw)

    assert [line["text"] for line in lines] == ["边界", "左上"]
    for line in lines:
        BoundingBox(*line["box"])  # must not raise
        x, y, width, height = line["box"]
        assert x >= 0.0 and y >= 0.0
        assert x + width <= 1.0
        assert y + height <= 1.0


def test_mineru_extract_page_reports_block_labels_and_ranges() -> None:
    """Block labels and line ranges must survive into contract v2.

    This is the evidence the furniture filter, chapter detection and review-crop
    sizing depend on; flattening blocks away loses all of it.
    """

    raw = {
        "pages": [
            {
                "blocks": [
                    {"type": "page_header", "bbox": [0.8, 0.02, 0.9, 0.05],
                     "content": [{"type": "text", "content": "V."}]},
                    {"type": "title", "bbox": [0.1, 0.1, 0.5, 0.16],
                     "content": [{"type": "text", "content": "第 一 章"}]},
                    {"type": "text", "bbox": [0.1, 0.2, 0.9, 0.4],
                     "content": [
                         {"type": "text", "content": "正文一"},
                         {"type": "text", "content": "正文二"},
                     ]},
                    {"type": "page_footnote", "bbox": [0.1, 0.9, 0.9, 0.95],
                     "content": [{"type": "text", "content": "① 脚注"}]},
                    {"type": "image", "bbox": [0.1, 0.5, 0.9, 0.8], "content": []},
                ]
            }
        ]
    }

    lines, blocks = mineru_worker.extract_page(raw)

    assert [line["text"] for line in lines] == ["V.", "第 一 章", "正文一", "正文二", "① 脚注"]
    assert [(b["type"], b["line_start"], b["line_end"]) for b in blocks] == [
        ("header", 0, 1),
        ("title", 1, 2),
        ("text", 2, 4),
        ("footnote", 4, 5),
    ]
    # The image block produced no text lines, so it is not reported as covering any.
    assert all(b["line_end"] > b["line_start"] for b in blocks)


def test_mineru_extract_page_uses_item_bbox_and_score() -> None:
    """Per-item bbox and score give real line geometry and confidence."""

    raw = {
        "pages": [
            {
                "blocks": [
                    {
                        "type": "text",
                        "bbox": [0.1, 0.2, 0.9, 0.4],
                        "content": [
                            {"type": "text", "content": "甲", "bbox": [0.1, 0.2, 0.9, 0.25],
                             "score": 0.42},
                            {"type": "text", "content": "乙"},
                        ],
                    }
                ]
            }
        ]
    }

    lines, _ = mineru_worker.extract_page(raw)

    assert lines[0]["box"] == pytest.approx([0.1, 0.2, 0.8, 0.05])
    assert lines[0]["confidence"] == pytest.approx(0.42)
    # No score reported: absence stays absent rather than becoming a fake value.
    assert "confidence" not in lines[1]
    # Falling back to the block box is explicit, not invented geometry.
    assert lines[1]["box"] == pytest.approx([0.1, 0.2, 0.8, 0.2])


def test_mineru_main_emits_contract_v2(monkeypatch, capsys, tmp_path: Path) -> None:
    raw = {
        "metadata": {"producer": {"name": "mineru", "version": "4.1.0"}},
        "pages": [
            {
                "blocks": [
                    {"type": "title", "bbox": [0.1, 0.1, 0.5, 0.2],
                     "content": [{"type": "text", "content": "第 一 章"}]}
                ]
            }
        ],
    }
    image = tmp_path / "page.png"
    image.write_bytes(b"fixture")

    def fake_run(*args, **kwargs):
        output_dir = Path(args[0][4])
        (output_dir / "page.json").write_text(json.dumps(raw), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mineru_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["mineru-worker", "--image", str(image), "--page-index", "3"])

    mineru_worker.main()

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["schema_version"] == 2
    assert emitted["blocks"][0]["type"] == "title"


def test_paddle_worker_reports_no_blocks_without_layout_output() -> None:
    """The plain PP-OCR path must not claim layout evidence it does not have."""

    raw = {
        "rec_texts": ["正文"],
        "rec_scores": [0.99],
        "rec_polys": [[[0, 0], [10, 0], [10, 10], [0, 10]]],
        "width": 100,
        "height": 100,
    }

    assert paddle_worker._layout_blocks(raw) == []


def test_paddle_worker_maps_ppstructure_layout_labels() -> None:
    raw = {
        "layout_det_res": [
            {"label": "title", "bbox": [0.1, 0.1, 0.5, 0.2]},
            {"label": "figure", "bbox": [0.1, 0.3, 0.9, 0.6]},
        ]
    }

    blocks = paddle_worker._layout_blocks(raw)

    assert [block["type"] for block in blocks] == ["title", "figure"]
    assert blocks[0]["box"] == pytest.approx([0.1, 0.1, 0.4, 0.1])
