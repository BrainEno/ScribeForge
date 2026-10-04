from __future__ import annotations

from collections.abc import Collection

import pytest

from scribeforge.domain.ocr import BoundingBox
from scribeforge.domain.risk import ReviewCandidate, RiskReason
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.vlm_review import ReviewEvidence, review_pending_candidates


def _candidate(pair_index: int, page_index: int = 4) -> ReviewCandidate:
    return ReviewCandidate(
        page_index=page_index,
        pair_index=pair_index,
        score=0.8,
        reasons=(RiskReason.TEXT_DISAGREEMENT,),
        crop=BoundingBox(0.1, 0.2, 0.3, 0.1),
    )


class RecordingReviewer:
    def __init__(self, *, fail_on_pair: int | None = None, wrong_identity: bool = False) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.fail_on_pair = fail_on_pair
        self.wrong_identity = wrong_identity

    def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
        self.calls.append((image_path, page_index, pair_index))
        if pair_index == self.fail_on_pair:
            raise RuntimeError("fixture worker failure")
        returned_pair = pair_index + 1 if self.wrong_identity else pair_index
        return VLMReading(
            page_index=page_index,
            pair_index=returned_pair,
            model="fixture-vlm",
            model_version="1",
            text=f"literal-{pair_index}",
            uncertain=False,
        )


class RecordingState:
    def __init__(self, completed: Collection[int] = ()) -> None:
        self._completed = set(completed)
        self.evidence: list[ReviewEvidence] = []

    def completed_pairs(self, page_index: int) -> Collection[int]:
        assert page_index == 4
        return frozenset(self._completed)

    def append(self, evidence: ReviewEvidence) -> None:
        self.evidence.append(evidence)
        self._completed.add(evidence.reading.pair_index)


def test_resume_skips_pairs_that_already_have_persisted_vlm_evidence() -> None:
    reviewer = RecordingReviewer()
    state = RecordingState(completed=(1,))
    candidates = (_candidate(1), _candidate(2), _candidate(3))
    crop_paths = {2: "crop-2.png", 3: "crop-3.png"}

    readings = review_pending_candidates(candidates, crop_paths, reviewer, state)

    assert reviewer.calls == [
        ("crop-2.png", 4, 2),
        ("crop-3.png", 4, 3),
    ]
    assert [reading.pair_index for reading in readings] == [2, 3]
    assert [item.reading.pair_index for item in state.evidence] == [2, 3]


def test_each_success_is_appended_before_the_next_candidate_runs() -> None:
    state = RecordingState()
    failing = RecordingReviewer(fail_on_pair=2)
    candidates = (_candidate(1), _candidate(2), _candidate(3))
    crop_paths = {1: "crop-1.png", 2: "crop-2.png", 3: "crop-3.png"}

    with pytest.raises(RuntimeError, match="worker failure"):
        review_pending_candidates(candidates, crop_paths, failing, state)

    assert [item.reading.pair_index for item in state.evidence] == [1]

    resumed = RecordingReviewer()
    readings = review_pending_candidates(candidates, crop_paths, resumed, state)

    assert resumed.calls == [
        ("crop-2.png", 4, 2),
        ("crop-3.png", 4, 3),
    ]
    assert [reading.pair_index for reading in readings] == [2, 3]


def test_missing_crop_is_required_only_for_pending_candidates() -> None:
    reviewer = RecordingReviewer()
    state = RecordingState(completed=(1,))

    with pytest.raises(KeyError, match="pair 2"):
        review_pending_candidates(
            (_candidate(1), _candidate(2)),
            crop_paths={},
            reviewer=reviewer,
            state=state,
        )

    assert reviewer.calls == []


def test_identity_mismatch_is_never_persisted() -> None:
    reviewer = RecordingReviewer(wrong_identity=True)
    state = RecordingState()

    with pytest.raises(ValueError, match="identity"):
        review_pending_candidates(
            (_candidate(2),),
            {2: "crop-2.png"},
            reviewer,
            state,
        )

    assert state.evidence == []


def test_batch_must_contain_candidates_from_one_page() -> None:
    reviewer = RecordingReviewer()
    state = RecordingState()

    with pytest.raises(ValueError, match="same page"):
        review_pending_candidates(
            (_candidate(1, page_index=4), _candidate(2, page_index=5)),
            {1: "crop-1.png", 2: "crop-2.png"},
            reviewer,
            state,
        )

    assert reviewer.calls == []
