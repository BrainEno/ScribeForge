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
