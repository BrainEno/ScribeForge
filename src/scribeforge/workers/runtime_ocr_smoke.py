"""Run a managed OCR worker against a synthetic page, never a user's book."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stdout
from io import BytesIO, TextIOWrapper
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageFont


def check_sample(worker: Callable[[], None]) -> None:
    with TemporaryDirectory(prefix="scribeforge-ocr-check-") as directory:
        path = Path(directory) / "sample.png"
        with Image.new("RGB", (1024, 320), "white") as image:
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=56)
            draw.text((80, 75), "ScribeForge OCR 2026", fill="black", font=font)
            draw.text((80, 175), "Local document recognition", fill="black", font=font)
            image.save(path)
        original_argv = sys.argv
        try:
            sys.argv = [original_argv[0], "--image", str(path), "--page-index", "0"]
            # Workers write UTF-8 to stdout.buffer; engine logs remain on stderr.
            with TextIOWrapper(BytesIO(), encoding="utf-8") as output:
                with redirect_stdout(output):
                    worker()
                output.flush()
                result = json.loads(output.buffer.getvalue())
        finally:
            sys.argv = original_argv
    text = "".join(line["text"] for line in result["lines"])
    normalized = "".join(char for char in text.casefold() if char.isalnum())
    if "scribeforgeocr2026" not in normalized and "localdocumentrecognition" not in normalized:
        raise ValueError("OCR could not recognize the built-in sample text")


def main() -> None:
    backend = sys.argv[1]
    if backend == "mineru":
        from scribeforge.workers.mineru_worker import main as worker
    elif backend == "paddleocr":
        from scribeforge.workers.paddle_worker import main as worker
    else:
        raise ValueError(f"Unsupported OCR sample backend: {backend}")
    os.environ["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), os.getenv("PATH", "")))
    check_sample(worker)


if __name__ == "__main__":
    main()
