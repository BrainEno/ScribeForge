from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any

from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult


class SubprocessOCREngine:
    """Adapter for isolated OCR workers using a versioned JSON stdout contract."""

    def __init__(self, name: str, command: Sequence[str]) -> None:
        self._name = name
        self._command = tuple(command)

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        completed = subprocess.run(
            [*self._command, "--image", image_path, "--page-index", str(page_index)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        raw: Any = json.loads(completed.stdout)
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("unsupported OCR worker schema")
        if raw.get("engine") != self._name:
            raise ValueError("OCR worker engine identity mismatch")
        if raw.get("page_index") != page_index:
            raise ValueError("OCR worker page identity mismatch")

        lines = tuple(self._line(item) for item in raw.get("lines", []))
        return PageOCRResult(
            page_index=page_index,
            engine=self._name,
            engine_version=str(raw["engine_version"]),
            lines=lines,
        )

    @staticmethod
    def _box(values: Any) -> BoundingBox:
        if not isinstance(values, list) or len(values) != 4:
            raise ValueError("OCR worker box must contain x, y, width, height")
        return BoundingBox(*(float(value) for value in values))

    @classmethod
    def _line(cls, raw: Any) -> OCRLine:
        if not isinstance(raw, dict):
            raise TypeError("OCR worker line must be an object")
        raw_tokens = raw.get("tokens", [])
        tokens = tuple(
            OCRToken(
                text=str(token["text"]),
                confidence=(
                    None if token.get("confidence") is None else float(token["confidence"])
                ),
                box=cls._box(token["box"]),
            )
            for token in raw_tokens
        )
        return OCRLine(text=str(raw["text"]), box=cls._box(raw["box"]), tokens=tokens)
