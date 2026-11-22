"""Book-wide furniture evidence, bound to the runs used by verification.

Line indexes belong to one OCR run only. Never transfer an index to the other
engine or treat one detected line as permission to omit a whole mixed group.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from scribeforge.domain.alignment import AlignmentStatus, LineAlignment
from scribeforge.domain.furniture import detect_page_furniture
from scribeforge.domain.ocr import PageOCRResult
from scribeforge.domain.text_assembly import assemble_group_text
from scribeforge.storage.sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class PageFurniture:
    primary: Mapping[int, str] = field(default_factory=dict)
    secondary: Mapping[int, str] = field(default_factory=dict)


def project_furniture(store: SQLiteStore, project_id: int) -> Mapping[int, PageFurniture]:
    """Detect each engine independently using only latest verification-bound runs.

    Unverified, historical and crop re-reads are not evidence for the latest
    alignment. Results from the two engines are never mixed for repetition counts.
    """
    primary: dict[int, PageOCRResult] = {}
    secondary: dict[int, PageOCRResult] = {}
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            continue
        record = store.load_verification(verification_id)
        primary[page.page_index] = store.load_ocr_run(record.primary_run_id)
        secondary[page.page_index] = store.load_ocr_run(record.secondary_run_id)
    first = detect_page_furniture(primary)
    second = detect_page_furniture(secondary)
    return {
        index: PageFurniture(first.get(index, {}), second.get(index, {}))
        for index in first.keys() | second.keys()
    }


def pair_furniture_reason(page: PageFurniture | None, pair: LineAlignment) -> str | None:
    """Return a reason only when *every* member is independently furniture.

    An unmatched line may be classified using its own engine's evidence. A
    matched/grouped pair is kept if either side includes an unclassified line,
    including an ambiguous group. This is conservative: uncertain furniture may
    remain, but unrelated text is never discarded merely because a header shares
    its alignment group or the same integer index in a different run.
    """
    if page is None or pair.status is AlignmentStatus.AMBIGUOUS:
        return None
    reasons: list[str] = []
    for indexes, detected in (
        (pair.primary_line_indexes, page.primary),
        (pair.secondary_line_indexes, page.secondary),
    ):
        for index in indexes:
            reason = detected.get(index)
            if reason is None:
                return None
            reasons.append(reason)
    return reasons[0] if reasons else None


def decision_preserves_furniture(
    text: str,
    pair: LineAlignment,
    primary: PageOCRResult,
    secondary: PageOCRResult,
) -> bool:
    """Do not erase a human or model correction that differs from both readings.

    Exact source text only; no fuzzy normalization can turn an editorial change
    into an authorization to discard it. This does not certify either reading.
    """
    candidates = (
        assemble_group_text(primary.lines[index].text for index in pair.primary_line_indexes)
        if pair.primary_line_indexes
        else None,
        assemble_group_text(secondary.lines[index].text for index in pair.secondary_line_indexes)
        if pair.secondary_line_indexes
        else None,
    )
    return text in candidates
