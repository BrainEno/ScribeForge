import sys

import pytest

from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine


def test_subprocess_adapter_passes_environment_to_worker(tmp_path) -> None:
    script = tmp_path / "worker.py"
    script.write_text(
        "import json, os\n"
        "print(json.dumps({'schema_version': 1, 'engine': 'fixture', "
        "'engine_version': '9', 'page_index': 0, 'lines': "
        "[{'text': os.environ.get('SCRIBEFORGE_TEST', 'unset'), "
        "'box': [0, 0, 0.5, 0.5], 'tokens': []}]}))\n",
        encoding="utf-8",
    )
    image = tmp_path / "page.png"
    image.write_bytes(b"x")

    engine = SubprocessOCREngine(
        "fixture",
        [sys.executable, str(script)],
        environment={"SCRIBEFORGE_TEST": "runtime-env"},
    )

    assert engine.analyze_page(str(image), 0).lines[0].text == "runtime-env"


def test_subprocess_adapter_runs_preflight_before_worker(tmp_path) -> None:
    marker = tmp_path / "ran"
    script = tmp_path / "worker.py"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )

    def preflight() -> None:
        raise RuntimeError("backend not ready")

    engine = SubprocessOCREngine(
        "fixture", [sys.executable, str(script)], preflight=preflight
    )

    with pytest.raises(RuntimeError, match="backend not ready"):
        engine.analyze_page(str(tmp_path / "page.png"), 0)
    assert not marker.exists()


def test_subprocess_adapter_maps_versioned_json_to_domain(tmp_path) -> None:
    script = tmp_path / "worker.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'schema_version': 1, 'engine': 'fixture', "
        "'engine_version': '9', 'page_index': 2, 'lines': "
        "[{'text':'乌鸦','box':[0.1,0.2,0.3,0.1],'tokens':[]}]}))\n",
        encoding="utf-8",
    )
    image = tmp_path / "page.png"
    image.write_bytes(b"x")
    engine = SubprocessOCREngine("fixture", [sys.executable, str(script)])

    result = engine.analyze_page(str(image), 2)

    assert result.engine == "fixture"
    assert result.lines[0].text == "乌鸦"
    assert result.lines[0].box.x == 0.1


def test_subprocess_adapter_rejects_wrong_schema(tmp_path) -> None:
    script = tmp_path / "worker.py"
    script.write_text("print('{\"schema_version\": 99}')\n", encoding="utf-8")
    image = tmp_path / "page.png"
    image.write_bytes(b"x")

    with pytest.raises(ValueError, match="unsupported OCR worker schema"):
        SubprocessOCREngine("fixture", [sys.executable, str(script)]).analyze_page(str(image), 0)


def _engine() -> object:
    from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine

    return SubprocessOCREngine("fixture", ("worker",))


def test_adapter_parses_contract_v2_blocks_and_direction() -> None:
    """Blocks, labels and direction must reach the domain layer."""

    from scribeforge.domain.ocr import BlockType, PageDirection

    raw = {
        "schema_version": 2,
        "engine": "fixture",
        "engine_version": "1.0",
        "page_index": 0,
        "direction": "vertical-rl",
        "lines": [
            {"text": "V．", "box": [0.8, 0.02, 0.1, 0.03], "tokens": []},
            {"text": "第一行", "box": [0.7, 0.1, 0.05, 0.8], "tokens": [],
             "confidence": 0.55},
        ],
        "blocks": [
            {"type": "header", "box": [0.8, 0.02, 0.1, 0.03], "line_start": 0, "line_end": 1},
            {"type": "text", "box": [0.7, 0.1, 0.05, 0.8], "line_start": 1, "line_end": 2},
        ],
    }

    result = _engine().parse_document(raw, 0)

    assert result.direction is PageDirection.VERTICAL_RL
    assert result.direction_declared is True
    assert [block.block_type for block in result.blocks] == [BlockType.HEADER, BlockType.TEXT]
    assert result.lines[1].confidence == pytest.approx(0.55)
    # The header is page furniture and must not pollute the readable text layer.
    assert result.text_line_indexes == (1,)


def test_adapter_treats_a_v1_document_as_blockless_evidence() -> None:
    """Old workers keep working: no blocks means every line counts as text."""

    from scribeforge.domain.ocr import PageDirection

    raw = {
        "schema_version": 1,
        "engine": "fixture",
        "engine_version": "1.0",
        "page_index": 0,
        "lines": [
            {"text": "甲", "box": [0.1, 0.1, 0.5, 0.05], "tokens": []},
            {"text": "乙", "box": [0.1, 0.2, 0.5, 0.05], "tokens": []},
        ],
    }

    result = _engine().parse_document(raw, 0)

    assert result.blocks == ()
    assert result.text_line_indexes == (0, 1)
    # A v1 worker has no way to report a direction, so the default value must
    # not masquerade as a declaration: it would silently disable geometry-based
    # direction detection on every page of a vertical book.
    assert result.direction is PageDirection.HORIZONTAL_LR
    assert result.direction_declared is False


def test_adapter_maps_unknown_block_labels_instead_of_failing() -> None:
    from scribeforge.domain.ocr import BlockType

    raw = {
        "schema_version": 2,
        "engine": "fixture",
        "engine_version": "1.0",
        "page_index": 0,
        "lines": [{"text": "甲", "box": [0.1, 0.1, 0.5, 0.05], "tokens": []}],
        "blocks": [
            {"type": "some_new_label", "box": [0.1, 0.1, 0.5, 0.05],
             "line_start": 0, "line_end": 1}
        ],
    }

    result = _engine().parse_document(raw, 0)

    assert result.blocks[0].block_type is BlockType.UNKNOWN
    # Unknown is neither furniture nor a figure, so the text is retained.
    assert result.text_line_indexes == (0,)


def test_adapter_rejects_a_block_range_beyond_reported_lines() -> None:
    raw = {
        "schema_version": 2,
        "engine": "fixture",
        "engine_version": "1.0",
        "page_index": 0,
        "lines": [{"text": "甲", "box": [0.1, 0.1, 0.5, 0.05], "tokens": []}],
        "blocks": [
            {"type": "text", "box": [0.1, 0.1, 0.5, 0.05], "line_start": 0, "line_end": 9}
        ],
    }

    with pytest.raises(ValueError, match="block line range exceeds"):
        _engine().parse_document(raw, 0)
