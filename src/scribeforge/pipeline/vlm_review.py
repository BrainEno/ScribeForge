from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from scribeforge.domain.risk import ReviewCandidate
from scribeforge.domain.vlm import VLMReading, VLMReviewer
from scribeforge.pipeline.crop_evidence import isolated_crop
from scribeforge.storage.sqlite import CropInputEvidence


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    reading: VLMReading
    crop_path: str
    crop_input: CropInputEvidence | None = None


class VLMReviewState(Protocol):
    def completed_pairs(self, page_index: int) -> Collection[int]: ...

    def append(self, evidence: ReviewEvidence) -> None: ...


def review_pending_candidates(
    candidates: Sequence[ReviewCandidate],
    crop_paths: Mapping[int, str],
    reviewer: VLMReviewer,
    state: VLMReviewState,
    *,
    crop_inputs: Mapping[int, CropInputEvidence] | None = None,
) -> tuple[VLMReading, ...]:
    if not candidates:
        return ()

    page_index = candidates[0].page_index
    if any(candidate.page_index != page_index for candidate in candidates):
        raise ValueError("VLM review batch candidates must describe the same page")

    completed = set(state.completed_pairs(page_index))
    readings: list[VLMReading] = []
    for candidate in candidates:
        if candidate.pair_index in completed:
            continue
        try:
            crop_path = crop_paths[candidate.pair_index]
        except KeyError as exc:
            raise KeyError(
                f"missing review crop for pending pair {candidate.pair_index}; "
                "whole-page fallback is forbidden"
            ) from exc

        crop_input = crop_inputs[candidate.pair_index] if crop_inputs is not None else None
        if crop_input is not None:
            with isolated_crop(crop_input) as private_path:
                reading = reviewer.transcribe_crop(
                    private_path, page_index=candidate.page_index, pair_index=candidate.pair_index
                )
        else:
            reading = reviewer.transcribe_crop(
                crop_path, page_index=candidate.page_index, pair_index=candidate.pair_index
            )
        if reading.page_index != candidate.page_index or reading.pair_index != candidate.pair_index:
            raise ValueError("VLM reading identity does not match review candidate")

        state.append(ReviewEvidence(reading=reading, crop_path=crop_path, crop_input=crop_input))
        completed.add(candidate.pair_index)
        readings.append(reading)

    return tuple(readings)
