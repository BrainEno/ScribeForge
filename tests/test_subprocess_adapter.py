import json
import sys

import pytest

from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine


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
