from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from scribeforge.domain.risk import ReviewCandidate


LITERAL_TRANSCRIPTION_POLICY = """Transcribe visible text only.
Preserve punctuation exactly as visible.
Preserve line breaks exactly as visible.
Do not correct grammar, spelling, syntax, wording, or style.
Do not infer missing text from context and do not complete sentences.
If any character or mark is genuinely uncertain, report the reading as uncertain.
Return only transcription evidence; never rewrite the source into smoother prose.
"""


@dataclass(frozen=True, slots=True)
class VLMReading:
    page_index: int
    pair_index: int
    model: str
    model_version: str
    text: str
    uncertain: bool

    def __post_init__(self) -> None:
        if self.page_index < 0 or self.pair_index < 0:
            raise ValueError("VLM reading indexes must be non-negative")
        if not self.model.strip():
            raise ValueError("VLM model identity is required")


class VLMReviewer(Protocol):
    def transcribe_crop(
        self, image_path: str, page_index: int, pair_index: int
    ) -> VLMReading: ...


def review_candidates_with_vlm(
    candidates: Sequence[ReviewCandidate],
    crop_paths: Mapping[int, str],
    reviewer: VLMReviewer,
) -> tuple[VLMReading, ...]:
    readings: list[VLMReading] = []
    for candidate in candidates:
        try:
            crop_path = crop_paths[candidate.pair_index]
        except KeyError as exc:
            raise KeyError(
                f"missing review crop for pair {candidate.pair_index}; whole-page fallback is forbidden"
            ) from exc
        reading = reviewer.transcribe_crop(
            crop_path,
            page_index=candidate.page_index,
            pair_index=candidate.pair_index,
        )
        if reading.page_index != candidate.page_index or reading.pair_index != candidate.pair_index:
            raise ValueError("VLM reading identity does not match review candidate")
        readings.append(reading)
    return tuple(readings)
