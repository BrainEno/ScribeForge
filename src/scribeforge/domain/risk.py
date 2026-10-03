from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum

from scribeforge.domain.alignment import ConflictKind, LineAlignment, PageAlignment
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult


class RiskReason(str, Enum):
    TEXT_DISAGREEMENT = "text_disagreement"
    PUNCTUATION_DISAGREEMENT = "punctuation_disagreement"
    MISSING_OR_EXTRA_LINE = "missing_or_extra_line"
    LOW_CONFIDENCE = "low_confidence"
    UNICODE_ANOMALY = "unicode_anomaly"
    SPATIAL_MISMATCH = "spatial_mismatch"


@dataclass(frozen=True, slots=True)
class PairRisk:
    pair_index: int
    score: float
    reasons: tuple[RiskReason, ...]


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    page_index: int
    pair_index: int
    score: float
    reasons: tuple[RiskReason, ...]
    crop: BoundingBox


def _line(result: PageOCRResult, index: int | None) -> OCRLine | None:
    return None if index is None else result.lines[index]


def _confidence(line: OCRLine | None) -> float | None:
    if line is None:
        return None
    values = [token.confidence for token in line.tokens if token.confidence is not None]
    return min(values) if values else None


def _has_unicode_anomaly(text: str) -> bool:
    for char in text:
        if char == "\ufffd":
            return True
        if unicodedata.category(char).startswith("C") and char not in "\n\r\t":
            return True
    return False


def _pair_text(pair: LineAlignment, primary: PageOCRResult, secondary: PageOCRResult) -> str:
    left = _line(primary, pair.primary_line_index)
    right = _line(secondary, pair.secondary_line_index)
    return "".join(line.text for line in (left, right) if line is not None)


def assess_page(
    alignment: PageAlignment,
    primary: PageOCRResult,
    secondary: PageOCRResult,
) -> tuple[PairRisk, ...]:
    if alignment.page_index != primary.page_index or alignment.page_index != secondary.page_index:
        raise ValueError("alignment and OCR evidence must describe the same page")

    assessments: list[PairRisk] = []
    for pair_index, pair in enumerate(alignment.pairs):
        score = 0.0
        reasons: list[RiskReason] = []
        kinds = {conflict.kind for conflict in pair.conflicts}

        if ConflictKind.SUBSTITUTION in kinds:
            score += 0.45
            reasons.append(RiskReason.TEXT_DISAGREEMENT)
        if ConflictKind.PUNCTUATION in kinds:
            score += 0.20
            reasons.append(RiskReason.PUNCTUATION_DISAGREEMENT)
        if ConflictKind.INSERTION in kinds or ConflictKind.DELETION in kinds:
            score += 0.55
            reasons.append(RiskReason.MISSING_OR_EXTRA_LINE)

        left = _line(primary, pair.primary_line_index)
        right = _line(secondary, pair.secondary_line_index)
        confidences = [value for value in (_confidence(left), _confidence(right)) if value is not None]
        if confidences and min(confidences) < 0.80:
            score += 0.25
            reasons.append(RiskReason.LOW_CONFIDENCE)

        if _has_unicode_anomaly(_pair_text(pair, primary, secondary)):
            score += 0.35
            reasons.append(RiskReason.UNICODE_ANOMALY)

        if pair.vertical_distance is not None and pair.vertical_distance > 0.04:
            score += 0.15
            reasons.append(RiskReason.SPATIAL_MISMATCH)

        assessments.append(PairRisk(pair_index, min(score, 1.0), tuple(reasons)))

    return tuple(assessments)


def _union_boxes(*boxes: BoundingBox) -> BoundingBox:
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.x + box.width for box in boxes)
    bottom = max(box.y + box.height for box in boxes)
    return BoundingBox(left, top, right - left, bottom - top)


def _candidate_crop(
    pair: LineAlignment,
    primary: PageOCRResult,
    secondary: PageOCRResult,
) -> BoundingBox:
    boxes = tuple(
        line.box
        for line in (
            _line(primary, pair.primary_line_index),
            _line(secondary, pair.secondary_line_index),
        )
        if line is not None
    )
    if not boxes:
        raise ValueError("review candidate requires OCR geometry")
    return _union_boxes(*boxes)


def review_candidates(
    alignment: PageAlignment,
    risks: tuple[PairRisk, ...],
    primary: PageOCRResult,
    secondary: PageOCRResult,
    *,
    threshold: float = 0.35,
) -> tuple[ReviewCandidate, ...]:
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("review threshold must be between 0 and 1")
    if len(risks) != len(alignment.pairs):
        raise ValueError("risk assessments must match alignment pairs")

    candidates: list[ReviewCandidate] = []
    for risk in risks:
        if risk.score < threshold:
            continue
        pair = alignment.pairs[risk.pair_index]
        candidates.append(
            ReviewCandidate(
                page_index=alignment.page_index,
                pair_index=risk.pair_index,
                score=risk.score,
                reasons=risk.reasons,
                crop=_candidate_crop(pair, primary, secondary),
            )
        )
    return tuple(candidates)
