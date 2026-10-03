import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribeforge.workers import mineru_worker, paddle_worker


def test_mineru_extract_lines_supports_polygon_and_bbox() -> None:
    raw = {
        "width": 1000,
        "height": 2000,
        "lines": [
            {"text": "第一行", "polygon": [[100, 200], [300, 200], [300, 260], [100, 260]]},
            {"text": "第二行", "bbox": [200, 400, 500, 500]},
        ],
    }

    lines = mineru_worker.extract_lines(raw)

    assert lines[0]["box"] == [0.1, 0.1, 0.2, 0.03]
    assert lines[1]["box"] == [0.2, 0.2, 0.3, 0.05]


def test_mineru_extract_lines_rejects_bad_payloads() -> None:
    with pytest.raises(TypeError):
        mineru_worker.extract_lines([])
    with pytest.raises(ValueError, match="missing geometry"):
        mineru_worker.extract_lines({"lines": [{"text": "x"}]})


def test_mineru_main_emits_worker_contract(monkeypatch, capsys, tmp_path: Path) -> None:
    raw = {
        "version": "4.0.1",
        "width": 100,
        "height": 100,
        "lines": [{"text": "乌鸦", "bbox": [10, 20, 40, 30]}],
    }

    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=json.dumps(raw))

    image = tmp_path / "page.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(mineru_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["mineru-worker", "--image", str(image), "--page-index", "3"])

    mineru_worker.main()

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["engine"] == "mineru"
    assert emitted["engine_version"] == "4.0.1"
    assert emitted["page_index"] == 3
    assert emitted["lines"][0]["text"] == "乌鸦"


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


def test_paddle_main_writes_contract_from_saved_result(monkeypatch, capsys, tmp_path: Path) -> None:
    raw = {
        "version": "3.5.0",
        "width": 100,
        "height": 100,
        "rec_texts": ["问"],
        "rec_scores": [0.91],
        "rec_polys": [[[10, 20], [20, 20], [20, 30], [10, 30]]],
    }

    def fake_run(argv, **kwargs):
        output = Path(argv[argv.index("--save_path") + 1])
        output.write_text(json.dumps(raw), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    image = tmp_path / "page.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(paddle_worker.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["paddle-worker", "--image", str(image), "--page-index", "5"])

    paddle_worker.main()

    emitted = json.loads(capsys.readouterr().out)
    assert emitted["engine"] == "paddleocr"
    assert emitted["engine_version"] == "3.5.0"
    assert emitted["page_index"] == 5
    assert emitted["lines"][0]["text"] == "问"
