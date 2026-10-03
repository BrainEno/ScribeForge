from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

from scribeforge.domain.ocr import OCRLine, PageOCRResult


class ConflictKind(str, Enum):
    SUBSTITUTION = "substitution"
    INSERTION = "insertion"
    DELETION = "deletion"
    PUNCTUATION = "punctuation"


@dataclass(frozen=True, slots=True)
class AlignmentConflict:
    kind: ConflictKind
    primary_text: str
    secondary_text: str
    primary_start: int
    primary_end: int
    secondary_start: int
    secondary_end: int


@dataclass(frozen=True, slots=True)
class LineAlignment:
    primary_line_index: int | None
    secondary_line_index: int | None
    text_similarity: float
    vertical_distance: float | None
    conflicts: tuple[AlignmentConflict, ...]


@dataclass(frozen=True, slots=True)
class PageAlignment:
    page_index: int
    primary_engine: str
    secondary_engine: str
    pairs: tuple[LineAlignment, ...]


def _center_y(line: OCRLine) -> float:
    return line.box.y + line.box.height / 2


def _is_punctuation(text: str) -> bool:
    return bool(text) and all(unicodedata.category(char).startswith("P") for char in text)


def _classify_replace(primary: str, secondary: str) -> ConflictKind:
    if _is_punctuation(primary) and _is_punctuation(secondary):
        return ConflictKind.PUNCTUATION
    return ConflictKind.SUBSTITUTION


def _diff_text(primary: str, secondary: str) -> tuple[AlignmentConflict, ...]:
    matcher = SequenceMatcher(a=primary, b=secondary, autojunk=False)
    conflicts: list[AlignmentConflict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        left = primary[i1:i2]
        right = secondary[j1:j2]
        if tag == "replace":
            kind = _classify_replace(left, right)
        elif tag == "delete":
            kind = ConflictKind.DELETION
        else:
            kind = ConflictKind.INSERTION
        conflicts.append(AlignmentConflict(kind, left, right, i1, i2, j1, j2))
    return tuple(conflicts)


def _match_score(primary: OCRLine, secondary: OCRLine) -> tuple[float, float]:
    distance = abs(_center_y(primary) - _center_y(secondary))
    similarity = SequenceMatcher(a=primary.text, b=secondary.text, autojunk=False).ratio()
    return similarity, distance


def align_pages(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    *,
    max_vertical_distance: float = 0.08,
) -> PageAlignment:
    if primary.page_index != secondary.page_index:
        raise ValueError("OCR results must describe the same page")

    remaining_secondary = set(range(len(secondary.lines)))
    pairs: list[LineAlignment] = []

    for primary_index, primary_line in enumerate(primary.lines):
        candidates: list[tuple[float, float, int]] = []
        for secondary_index in remaining_secondary:
            similarity, distance = _match_score(primary_line, secondary.lines[secondary_index])
            if distance <= max_vertical_distance:
                candidates.append((similarity, -distance, secondary_index))

        if not candidates:
            pairs.append(
                LineAlignment(
                    primary_line_index=primary_index,
                    secondary_line_index=None,
                    text_similarity=0.0,
                    vertical_distance=None,
                    conflicts=(
                        AlignmentConflict(
                            ConflictKind.DELETION,
                            primary_line.text,
                            "",
                            0,
                            len(primary_line.text),
                            0,
                            0,
                        ),
                    ),
                )
            )
            continue

        similarity, negative_distance, secondary_index = max(candidates)
        remaining_secondary.remove(secondary_index)
        secondary_line = secondary.lines[secondary_index]
        pairs.append(
            LineAlignment(
                primary_line_index=primary_index,
                secondary_line_index=secondary_index,
                text_similarity=similarity,
                vertical_distance=-negative_distance,
                conflicts=_diff_text(primary_line.text, secondary_line.text),
            )
        )

    for secondary_index in sorted(remaining_secondary):
        secondary_line = secondary.lines[secondary_index]
        pairs.append(
            LineAlignment(
                primary_line_index=None,
                secondary_line_index=secondary_index,
                text_similarity=0.0,
                vertical_distance=None,
                conflicts=(
                    AlignmentConflict(
                        ConflictKind.INSERTION,
                        "",
                        secondary_line.text,
                        0,
                        0,
                        0,
                        len(secondary_line.text),
                    ),
                ),
            )
        )

    return PageAlignment(
        page_index=primary.page_index,
        primary_engine=primary.engine,
        secondary_engine=secondary.engine,
        pairs=tuple(pairs),
    )
