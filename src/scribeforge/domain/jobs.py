from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobStage(str, Enum):
    IMPORT = "import"
    PREPROCESS = "preprocess"
    PRIMARY_OCR = "primary_ocr"
    SECONDARY_OCR = "secondary_ocr"
    VERIFY = "verify"
    VLM_REVIEW = "vlm_review"

    @property
    def page_scoped(self) -> bool:
        return self is not JobStage.IMPORT

    @property
    def order(self) -> int:
        """Position in the per-page pipeline.

        Used wherever stages must be reasoned about as a sequence rather than a
        set: preprocessing has to finish before any engine reads the raster, and
        an export must not claim a page is done while a later stage is pending.
        """

        return _STAGE_ORDER[self]


_STAGE_ORDER: dict[JobStage, int] = {
    JobStage.IMPORT: 0,
    JobStage.PREPROCESS: 1,
    JobStage.PRIMARY_OCR: 2,
    JobStage.SECONDARY_OCR: 3,
    JobStage.VERIFY: 4,
    JobStage.VLM_REVIEW: 5,
}

# Stages that must be complete before a page's text can be trusted downstream.
OCR_STAGES: tuple[JobStage, ...] = (
    JobStage.PREPROCESS,
    JobStage.PRIMARY_OCR,
    JobStage.SECONDARY_OCR,
    JobStage.VERIFY,
)


def stage_before(left: JobStage, right: JobStage) -> bool:
    """True when ``left`` runs earlier than ``right`` in the page pipeline."""

    return left.order < right.order


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: int
    project_id: int
    page_index: int | None
    stage: JobStage
    state: JobState
    attempts: int
    last_error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
