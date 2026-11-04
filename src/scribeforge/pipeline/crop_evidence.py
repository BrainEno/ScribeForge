"""Identity and page-frame geometry for an isolated model input."""

from pathlib import Path

from PIL import Image

from scribeforge.domain.crops import ReviewCropPlan
from scribeforge.domain.page_asset import PageAssetError, file_sha256
from scribeforge.storage.sqlite import (
    CropInputEvidence,
    OCRInputFrame,
    SQLiteStore,
    VLMReviewRecord,
)


def crop_evidence(
    path: str, plan: ReviewCropPlan, frame: OCRInputFrame, verification_id: int
) -> CropInputEvidence:
    with Image.open(path) as image:
        if image.size != (plan.box.width * plan.scale, plan.box.height * plan.scale):
            raise PageAssetError("crop dimensions do not match the recorded geometry")
    return CropInputEvidence(
        verification_id,
        plan.pair_index,
        frame.sha256,
        file_sha256(Path(path)),
        path,
        plan.box.x,
        plan.box.y,
        plan.box.width,
        plan.box.height,
        plan.scale,
    )


def verify_crop(evidence: CropInputEvidence) -> None:
    try:
        with Image.open(evidence.crop_path) as image:
            size = image.size
        digest = file_sha256(Path(evidence.crop_path))
    except OSError as exc:
        raise PageAssetError(f"crop evidence changed or missing: {exc}") from exc
    if (
        size != (evidence.width * evidence.scale, evidence.height * evidence.scale)
        or digest != evidence.crop_sha256
    ):
        raise PageAssetError("crop evidence changed; restore it from a verified backup")


def verified_vlm_crop(
    store: SQLiteStore, verification_id: int, frame: OCRInputFrame, review: VLMReviewRecord
) -> CropInputEvidence:
    """Validate the binding and bytes before reusing or displaying a VLM crop."""
    evidence = store.load_vlm_crop_input(review.id)
    if evidence is None:
        raise ValueError("VLM crop input is unknown for legacy evidence; re-import into a new project")
    if (
        evidence.frame_sha256 != frame.sha256
        or evidence.verification_id != verification_id
        or review.verification_run_id != verification_id
        or evidence.pair_index != review.pair_index
        or evidence.crop_path != review.crop_path
        or evidence.crop_sha256 != review.crop_sha256
        or evidence.x + evidence.width > frame.width
        or evidence.y + evidence.height > frame.height
    ):
        raise ValueError("VLM crop evidence mismatch")
    verify_crop(evidence)
    return evidence
