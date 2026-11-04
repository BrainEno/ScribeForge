"""Identity and page-frame geometry for an isolated model input."""

from pathlib import Path

from PIL import Image

from scribeforge.domain.crops import ReviewCropPlan
from scribeforge.domain.page_asset import PageAssetError, file_sha256
from scribeforge.storage.sqlite import CropInputEvidence, OCRInputFrame


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
