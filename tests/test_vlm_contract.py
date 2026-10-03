import sys
from pathlib import Path

import pytest

from scribeforge.adapters.subprocess_vlm import SubprocessVLMReviewer
from scribeforge.domain.ocr import BoundingBox
from scribeforge.domain.risk import ReviewCandidate, RiskReason
from scribeforge.domain.vlm import LITERAL_TRANSCRIPTION_POLICY, VLMReading, review_candidates_with_vlm


def _candidate(pair_index: int = 2) -> ReviewCandidate:
    return ReviewCandidate(
        page_index=4,
        pair_index=pair_index,
        score=0.7,
        reasons=(RiskReason.TEXT_DISAGREEMENT,),
        crop=BoundingBox(0.1, 0.2, 0.3, 0.1),
    )


def test_literal_policy_forbids_editorial_smoothing() -> None:
    policy = LITERAL_TRANSCRIPTION_POLICY.lower()

    assert "visible text only" in policy
    assert "do not correct grammar" in policy
    assert "preserve punctuation" in policy
    assert "preserve line breaks" in policy
    assert "do not infer" in policy
    assert "uncertain" in policy


def test_vlm_reading_rejects_invalid_identity() -> None:
    with pytest.raises(ValueError, match="indexes"):
        VLMReading(-1, 0, "model", "1", "x", False)
    with pytest.raises(ValueError, match="model identity"):
        VLMReading(0, 0, " ", "1", "x", False)


def test_subprocess_vlm_maps_versioned_json_to_reading(tmp_path: Path) -> None:
    script = tmp_path / "worker.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'schema_version': 1, 'model': 'fixture-vlm', "
        "'model_version': '1', 'page_index': 4, 'pair_index': 2, "
        "'text': '乌鸦。', 'uncertain': False}))\n",
        encoding="utf-8",
    )
    image = tmp_path / "crop.png"
    image.write_bytes(b"fixture")
    reviewer = SubprocessVLMReviewer("fixture-vlm", [sys.executable, str(script)])

    reading = reviewer.transcribe_crop(str(image), page_index=4, pair_index=2)

    assert reading == VLMReading(
        page_index=4,
        pair_index=2,
        model="fixture-vlm",
        model_version="1",
        text="乌鸦。",
        uncertain=False,
    )


def test_subprocess_vlm_rejects_schema_or_identity_mismatch(tmp_path: Path) -> None:
    image = tmp_path / "crop.png"
    image.write_bytes(b"fixture")
    bad_schema = tmp_path / "schema.py"
    bad_schema.write_text("print('{\"schema_version\": 9}')\n", encoding="utf-8")

    with pytest.raises(ValueError, match="schema"):
        SubprocessVLMReviewer("fixture-vlm", [sys.executable, str(bad_schema)]).transcribe_crop(
            str(image), 4, 2
        )

    wrong_id = tmp_path / "identity.py"
    wrong_id.write_text(
        "import json\n"
        "print(json.dumps({'schema_version':1,'model':'other','model_version':'1',"
        "'page_index':4,'pair_index':2,'text':'x','uncertain':False}))\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="identity"):
        SubprocessVLMReviewer("fixture-vlm", [sys.executable, str(wrong_id)]).transcribe_crop(
            str(image), 4, 2
        )


def test_subprocess_vlm_requires_boolean_uncertainty(tmp_path: Path) -> None:
    script = tmp_path / "worker.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'schema_version':1,'model':'fixture-vlm','model_version':'1',"
        "'page_index':4,'pair_index':2,'text':'x','uncertain':'maybe'}))\n",
        encoding="utf-8",
    )
    image = tmp_path / "crop.png"
    image.write_bytes(b"fixture")

    with pytest.raises(TypeError, match="boolean"):
        SubprocessVLMReviewer("fixture-vlm", [sys.executable, str(script)]).transcribe_crop(
            str(image), 4, 2
        )


class RecordingReviewer:
    def __init__(self, wrong_identity: bool = False) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.wrong_identity = wrong_identity

    def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
        self.calls.append((image_path, page_index, pair_index))
        returned_pair = pair_index + 1 if self.wrong_identity else pair_index
        return VLMReading(page_index, returned_pair, "fake", "1", "literal", False)


def test_only_review_candidates_are_sent_to_vlm() -> None:
    reviewer = RecordingReviewer()
    candidates = (_candidate(2), _candidate(5))
    crop_paths = {2: "crop-2.png", 5: "crop-5.png"}

    readings = review_candidates_with_vlm(candidates, crop_paths, reviewer)

    assert reviewer.calls == [
        ("crop-2.png", 4, 2),
        ("crop-5.png", 4, 5),
    ]
    assert [reading.pair_index for reading in readings] == [2, 5]


def test_missing_crop_fails_instead_of_sending_whole_page() -> None:
    reviewer = RecordingReviewer()

    with pytest.raises(KeyError, match="crop"):
        review_candidates_with_vlm((_candidate(),), {}, reviewer)

    assert reviewer.calls == []


def test_candidate_and_vlm_identity_must_match() -> None:
    reviewer = RecordingReviewer(wrong_identity=True)

    with pytest.raises(ValueError, match="identity"):
        review_candidates_with_vlm((_candidate(),), {2: "crop.png"}, reviewer)
