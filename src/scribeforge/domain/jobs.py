from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobStage(str, Enum):
    IMPORT = "import"
    PRIMARY_OCR = "primary_ocr"
    SECONDARY_OCR = "secondary_ocr"
    VERIFY = "verify"
    VLM_REVIEW = "vlm_review"

    @property
    def page_scoped(self) -> bool:
        return self is not JobStage.IMPORT


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
