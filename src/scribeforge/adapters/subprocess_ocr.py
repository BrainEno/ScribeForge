from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from scribeforge.domain.ocr import (
    BlockType,
    BoundingBox,
    OCRBlock,
    OCRLine,
    OCRToken,
    PageDirection,
    PageOCRResult,
)
from scribeforge.workers.protocol import SUPPORTED_SCHEMA_VERSIONS, canonical_block_label


def block_type_from_label(label: object) -> BlockType:
    """Map an engine label to a :class:`BlockType` without ever failing a page.

    An unrecognised label becomes ``UNKNOWN`` rather than raising: a new engine
    category must not make previously readable pages unprocessable. The label
    aliases live in the worker contract so workers and the adapter agree.
    """

    return BlockType(canonical_block_label(label))


def direction_from_value(value: object) -> PageDirection:
    """Map a worker direction string, defaulting to horizontal left-to-right."""

    if isinstance(value, str):
        for direction in PageDirection:
            if direction.value == value.strip().lower():
                return direction
    return PageDirection.HORIZONTAL_LR


class SubprocessOCREngine:
    """Adapter for isolated OCR workers using a versioned JSON stdout contract.

    An optional environment is merged over the process environment, and an
    optional preflight hook runs before each page so a backend that is no longer
    healthy fails loudly instead of silently producing wrong evidence.
    """

    def __init__(
        self,
        name: str,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        preflight: Callable[[], None] | None = None,
    ) -> None:
        self._name = name
        self._command = tuple(command)
        self._environment = None if environment is None else dict(environment)
        self._preflight = preflight

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        if self._preflight is not None:
            self._preflight()
        process_env = None
        if self._environment:
            process_env = os.environ.copy()
            process_env.update(self._environment)
        completed = subprocess.run(
            [*self._command, "--image", image_path, "--page-index", str(page_index)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=process_env,
        )
        return self.parse_document(json.loads(completed.stdout), page_index)

    def parse_document(self, raw: Any, page_index: int) -> PageOCRResult:
        """Parse a worker result document of any supported schema version."""

        if not isinstance(raw, dict):
            raise TypeError("OCR worker output must be a JSON object")
        schema_version = raw.get("schema_version")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError("unsupported OCR worker schema")
        if raw.get("engine") != self._name:
            raise ValueError("OCR worker engine identity mismatch")
        if raw.get("page_index") != page_index:
            raise ValueError("OCR worker page identity mismatch")

        lines = tuple(self._line(item) for item in raw.get("lines", []))
        result = PageOCRResult(
            page_index=page_index,
            engine=self._name,
            engine_version=str(raw["engine_version"]),
            lines=lines,
            blocks=self._blocks(raw.get("blocks", []), line_count=len(lines)),
            direction=direction_from_value(raw.get("direction")),
            direction_declared=raw.get("direction") is not None,
        )
        return result

    @staticmethod
    def _box(values: Any) -> BoundingBox:
        if not isinstance(values, list) or len(values) != 4:
            raise ValueError("OCR worker box must contain x, y, width, height")
        return BoundingBox(*(float(value) for value in values))

    @staticmethod
    def _blocks(raw: Any, *, line_count: int) -> tuple[OCRBlock, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise TypeError("OCR worker blocks must be a list")

        blocks: list[OCRBlock] = []
        for item in raw:
            if not isinstance(item, dict):
                raise TypeError("OCR worker block must be an object")
            raw_start = item.get("line_start", 0)
            raw_end = item.get("line_end", line_count)
            if not isinstance(raw_start, int) or not isinstance(raw_end, int):
                raise TypeError("OCR worker block line range must be integers")
            blocks.append(
                OCRBlock(
                    block_type=block_type_from_label(item.get("type")),
                    box=SubprocessOCREngine._box(item.get("box")),
                    line_start=raw_start,
                    line_end=raw_end,
                )
            )
        return tuple(blocks)

    @classmethod
    def _line(cls, raw: Any) -> OCRLine:
        if not isinstance(raw, dict):
            raise TypeError("OCR worker line must be an object")
        raw_tokens = raw.get("tokens") or ()
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
        raw_confidence = raw.get("confidence")
        return OCRLine(
            text=str(raw["text"]),
            box=cls._box(raw["box"]),
            tokens=tokens,
            confidence=None if raw_confidence is None else float(raw_confidence),
        )
