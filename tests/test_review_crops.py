import pytest

from scribeforge.domain.crops import (
    PixelBox,
    ReviewCropPlan,
    plan_region_read,
    plan_review_crops,
    region_read_file_name,
    review_crop_file_name,
)
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

    assert plans[0].box == PixelBox(x=90, y=380, width=320, height=240)
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


def test_genuine_fractional_boundary_still_rounds_outward() -> None:
    candidate = _candidate(BoundingBox(0.1014, 0.1014, 0.0012, 0.0012))

    plan = plan_review_crops((candidate,), 1000, 1000, padding_fraction=0)[0]

    assert plan.box == PixelBox(101, 101, 2, 2)


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


def test_crop_file_name_is_derivable_and_guards_indexes() -> None:
    assert review_crop_file_name(7, 3) == "page-000008-pair-0003.png"
    assert review_crop_file_name(0, 0) == "page-000001-pair-0000.png"

    with pytest.raises(ValueError, match="non-negative"):
        review_crop_file_name(-1, 0)
    with pytest.raises(ValueError, match="non-negative"):
        review_crop_file_name(0, -1)


def test_region_read_plan_names_each_attempt_separately() -> None:
    """Round 3 must not overwrite the crop round 1 was judged from."""

    plan = plan_region_read(
        page_index=0,
        pair_index=3,
        box=BoundingBox(0.1, 0.2, 0.3, 0.05),
        page_width=1000,
        page_height=2000,
        attempt=2,
        padding_fraction=0.03,
        scale=2,
    )

    assert plan.file_name == "page-000001-pair-0003-read-02.png"
    assert plan.scale == 2
    assert plan.box == PixelBox(x=70, y=340, width=360, height=220)
    assert region_read_file_name(0, 3, 2) == plan.file_name


def test_region_read_plan_rejects_impossible_request() -> None:
    box = BoundingBox(0.1, 0.1, 0.1, 0.1)

    with pytest.raises(ValueError, match="attempt"):
        region_read_file_name(0, 0, 0)
    with pytest.raises(ValueError, match="scale"):
        plan_region_read(
            page_index=0,
            pair_index=0,
            box=box,
            page_width=100,
            page_height=100,
            attempt=1,
            padding_fraction=0.01,
            scale=0,
        )
    with pytest.raises(ValueError, match="page dimensions"):
        plan_region_read(
            page_index=0,
            pair_index=0,
            box=box,
            page_width=0,
            page_height=100,
            attempt=1,
            padding_fraction=0.01,
            scale=1,
        )


def test_crop_plan_rejects_a_zero_scale() -> None:
    with pytest.raises(ValueError, match="scale"):
        ReviewCropPlan(0, 0, PixelBox(0, 0, 1, 1), "crop.png", scale=0)
