from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum

from scribeforge.domain.alignment import ConflictKind, LineAlignment, PageAlignment
from scribeforge.domain.normalize import is_variant_only_difference
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult


class RiskReason(str, Enum):
    TEXT_DISAGREEMENT = "text_disagreement"
    VARIANT_DISAGREEMENT = "variant_disagreement"
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
    #: Lowest confidence either reader reported for the pair, or ``None`` when
    #: neither reported one. Kept separate from the score so an acceptance policy
    #: can apply a confidence floor without the score having to encode one.
    min_confidence: float | None = None
    #: Part of ``score`` that comes from *evidence quality* rather than from
    #: structural disagreement: low confidence, unicode anomalies, misaligned
    #: boxes. The acceptance gate reads this and not ``score``, because
    #: "the engines disagree and a vision model settled it" is a resolved question,
    #: while "the text is unreadable" is not — folding the two into one number made
    #: every vision-model resolution look as risky as an unreadable glyph.
    content_score: float = 0.0


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
    """Lowest known confidence for a line, or ``None`` when the engine gave none.

    Line-level confidence (worker contract v2) is considered alongside token
    confidence so a backend that scores whole lines can still raise the
    low-confidence risk. ``None`` stays "unknown" and never contributes a score:
    missing confidence must not be read as agreement.
    """

    return None if line is None else line.lowest_confidence


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

        substitutions = [
            conflict
            for conflict in pair.conflicts
            if conflict.kind is ConflictKind.SUBSTITUTION
        ]
        content_score = 0.0
        if substitutions:
            # A difference that disappears under glyph-variant folding is an
            # orthographic choice (繁/简, 异体字), not a reading disagreement.
            # Scoring it like a real substitution sends every mixed-script book
            # into human review while teaching reviewers to rubber-stamp.
            variant_only = all(
                is_variant_only_difference(conflict.primary_text, conflict.secondary_text)
                for conflict in substitutions
            )
            if variant_only:
                score += 0.10
                reasons.append(RiskReason.VARIANT_DISAGREEMENT)
            else:
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
            content_score += 0.25
            reasons.append(RiskReason.LOW_CONFIDENCE)

        if _has_unicode_anomaly(_pair_text(pair, primary, secondary)):
            score += 0.35
            content_score += 0.35
            reasons.append(RiskReason.UNICODE_ANOMALY)

        if pair.vertical_distance is not None and pair.vertical_distance > 0.04:
            score += 0.15
            content_score += 0.15
            reasons.append(RiskReason.SPATIAL_MISMATCH)

        assessments.append(
            PairRisk(
                pair_index,
                min(score, 1.0),
                tuple(reasons),
                min_confidence=min(confidences) if confidences else None,
                content_score=min(content_score, 1.0),
            )
        )

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
    if len(boxes) == 1:
        return boxes[0]
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
