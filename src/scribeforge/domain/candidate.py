from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OcrCandidatePage:
    """Unverified raw OCR output for one source page."""

    page_index: int
    run_id: int
    engine_version: str
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OcrCandidateBook:
    """Raw engine output for comparison; deliberately distinct from VerifiedBook."""

    source_title: str
    engine: str
    pages: tuple[OcrCandidatePage, ...]

    @property
    def engine_versions(self) -> tuple[str, ...]:
        return tuple(sorted({page.engine_version for page in self.pages}))
