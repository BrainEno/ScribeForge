import pytest

from scribeforge.domain.crops import PixelBox, plan_review_crops
from scribeforge.domain.ocr import BoundingBox
from scribeforge.domain.risk import ReviewCandidate, RiskReason


def _candidate(box: BoundingBox, pair_index: int = 3) -> ReviewCandidate:
    return ReviewCandidate(
        page_index=7,
        pair_index=pair_index,
        score=0.6,
        reasons=(RiskReason.TEXT_DISAGREEMENT,),
        crop=box,
    )


def test_normalized_candidate_becomes_padded_pixel_crop() -> None:
    candidate = _candidate(BoundingBox(0.10, 0.20, 0.30, 0.10))

    plans = plan_review_crops((candidate,), 1000, 2000, padding_fraction=0.01)

    assert plans[0].box == PixelBox(x=90, y=180, width=320, height=240)
    assert plans[0].page_index == 7
    assert plans[0].pair_index == 3
    assert plans[0].file_name == "page-000008-pair-0003.png"


def test_padding_is_clamped_to_page_edges() -> None:
    candidate = _candidate(BoundingBox(0.0, 0.0, 0.10, 0.10))

    plan = plan_review_crops((candidate,), 1000, 1000, padding_fraction=0.02)[0]

    assert plan.box == PixelBox(0, 0, 120, 120)


def test_pixel_rounding_never_drops_visible_candidate_area() -> None:
    candidate = _candidate(BoundingBox(0.101, 0.101, 0.001, 0.001))

    plan = plan_review_crops((candidate,), 1000, 1000, padding_fraction=0)[0]

    assert plan.box.x == 101
    assert plan.box.y == 101
    assert plan.box.width == 1
    assert plan.box.height == 1


def test_crop_planner_rejects_invalid_page_or_padding() -> None:
    candidate = _candidate(BoundingBox(0.1, 0.1, 0.1, 0.1))

    with pytest.raises(ValueError, match="page dimensions"):
        plan_review_crops((candidate,), 0, 1000)
    with pytest.raises(ValueError, match="padding"):
        plan_review_crops((candidate,), 1000, 1000, padding_fraction=-0.1)


def test_each_candidate_gets_stable_unique_name() -> None:
    candidates = (
        _candidate(BoundingBox(0.1, 0.1, 0.2, 0.1), pair_index=1),
        _candidate(BoundingBox(0.1, 0.3, 0.2, 0.1), pair_index=9),
    )

    plans = plan_review_crops(candidates, 1200, 1600)

    assert [plan.file_name for plan in plans] == [
        "page-000008-pair-0001.png",
        "page-000008-pair-0009.png",
    ]
