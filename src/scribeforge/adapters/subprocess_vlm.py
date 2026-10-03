from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from typing import Any

from scribeforge.domain.vlm import LITERAL_TRANSCRIPTION_POLICY, VLMReading


class SubprocessVLMReviewer:
    """Adapter for isolated local VLM workers using versioned JSON stdout."""

    def __init__(self, model: str, command: Sequence[str]) -> None:
        self._model = model
        self._command = tuple(command)

    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading:
        completed = subprocess.run(
            [
                *self._command,
                "--image",
                image_path,
                "--page-index",
                str(page_index),
                "--pair-index",
                str(pair_index),
                "--policy",
                LITERAL_TRANSCRIPTION_POLICY,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        raw: Any = json.loads(completed.stdout)
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("unsupported VLM worker schema")
        if raw.get("model") != self._model:
            raise ValueError("VLM worker model identity mismatch")
        if raw.get("page_index") != page_index or raw.get("pair_index") != pair_index:
            raise ValueError("VLM worker review identity mismatch")
        uncertain = raw.get("uncertain")
        if not isinstance(uncertain, bool):
            raise TypeError("VLM worker uncertain flag must be boolean")
        return VLMReading(
            page_index=page_index,
            pair_index=pair_index,
            model=self._model,
            model_version=str(raw["model_version"]),
            text=str(raw["text"]),
            uncertain=uncertain,
        )
