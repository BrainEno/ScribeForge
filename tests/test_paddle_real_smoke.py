from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scribeforge.adapters.runtime_workers import runtime_environment
from scribeforge.runtime.models import RuntimeLayout
from scribeforge.runtime.paths import default_runtime_root

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_IMAGE = (
    _REPOSITORY_ROOT / "benchmark" / "corpus" / "guji" / "images" / "page_001.png"
)
_RUN_REAL_SMOKE = os.environ.get("SCRIBEFORGE_RUN_PADDLE_SMOKE") == "1"


@pytest.mark.gpu
@pytest.mark.skipif(
    not _RUN_REAL_SMOKE,
    reason="set SCRIBEFORGE_RUN_PADDLE_SMOKE=1 to run the real PP-OCRv4 model smoke test",
)
def test_real_pp_ocrv4_worker_emits_native_word_geometry() -> None:
    """Exercise the worker against an installed PaddleOCR runtime and real weights.

    This is opt-in because PaddlePaddle and PP-OCRv4 weights are deliberately not
    installed or downloaded by the normal test suite.
    """

    runtime_root_value = os.environ.get("SCRIBEFORGE_RUNTIME_ROOT")
    runtime_root = (
        Path(runtime_root_value).expanduser().resolve()
        if runtime_root_value
        else default_runtime_root()
    )
    layout = RuntimeLayout(runtime_root)
    os_name = "windows" if os.name == "nt" else "linux"
    paddleocr_cli = layout.executable("paddleocr", "paddleocr", os_name)
    if not paddleocr_cli.is_file():
        pytest.fail(
            "PaddleOCR runtime is not installed at the configured runtime root; "
            f"expected {paddleocr_cli}. Set SCRIBEFORGE_RUNTIME_ROOT if needed."
        )

    image_value = os.environ.get("SCRIBEFORGE_PADDLE_SMOKE_IMAGE")
    image_path = (
        Path(image_value).expanduser().resolve()
        if image_value
        else _DEFAULT_IMAGE
    )
    if not image_path.is_file():
        pytest.fail(f"PaddleOCR smoke image does not exist: {image_path}")

    worker = _REPOSITORY_ROOT / "src" / "scribeforge" / "workers" / "paddle_worker.py"
    environment = os.environ.copy()
    environment.update(runtime_environment(layout))
    base_path = environment.get("PATH", "")
    environment["PATH"] = (
        f"{paddleocr_cli.parent}{os.pathsep}{base_path}"
        if base_path
        else str(paddleocr_cli.parent)
    )

    result = subprocess.run(
        [
            sys.executable,
            str(worker),
            "--image",
            str(image_path),
            "--page-index",
            "0",
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, (
        "PP-OCRv4 real-model worker failed. Check the PaddleOCR runtime/model setup.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    document = json.loads(result.stdout)
    assert document["schema_version"] == 2
    assert document["engine"] == "paddleocr"
    lines = document["lines"]
    assert isinstance(lines, list) and lines
    token_lines = [
        line
        for line in lines
        if isinstance(line, dict) and isinstance(line.get("tokens"), list) and line["tokens"]
    ]
    assert token_lines, (
        "PP-OCRv4 returned no non-empty native text_word/text_word_boxes; "
        "the worker must not synthesize tokens from line geometry"
    )

    for line in token_lines:
        confidence = line.get("confidence")
        assert isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
        assert 0.0 <= confidence <= 1.0
        for token in line["tokens"]:
            assert isinstance(token, dict)
            assert isinstance(token.get("text"), str) and token["text"]
            box = token.get("box")
            assert isinstance(box, list) and len(box) == 4
            x, y, width, height = box
            assert all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in box
            )
            assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
            assert 0.0 < width <= 1.0 and 0.0 < height <= 1.0
            assert x + width <= 1.000001 and y + height <= 1.000001
            assert "confidence" not in token, (
                "PP-OCRv4 only supplied line-level rec_score; do not copy it to each word"
            )
