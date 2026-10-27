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


def _match_primary_lines(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    max_vertical_distance: float,
) -> dict[int, tuple[int, float, float]]:
    """Assign each primary line index to at most one secondary line index.

    Candidate pairs are resolved by a global best-first greedy pass instead of
    scanning primary lines strictly in order: evaluating one primary line at a
    time and permanently removing its best remaining candidate can let an
    earlier line claim a secondary line that is actually a stronger match for
    a later one, stranding that later line with a poor match even though a
    better overall assignment exists. Sorting every candidate pair by
    similarity (distance as tie-breaker) and assigning greedily from the top
    approximates the globally optimal one-to-one assignment while remaining
    simple and deterministic.
    """
    candidates: list[tuple[float, float, int, int]] = []
    for primary_index, primary_line in enumerate(primary.lines):
        for secondary_index, secondary_line in enumerate(secondary.lines):
            similarity, distance = _match_score(primary_line, secondary_line)
            if distance <= max_vertical_distance:
                candidates.append((similarity, -distance, primary_index, secondary_index))

    candidates.sort(reverse=True)

    assigned_primary: set[int] = set()
    assigned_secondary: set[int] = set()
    matches: dict[int, tuple[int, float, float]] = {}
    for similarity, negative_distance, primary_index, secondary_index in candidates:
        if primary_index in assigned_primary or secondary_index in assigned_secondary:
            continue
        assigned_primary.add(primary_index)
        assigned_secondary.add(secondary_index)
        matches[primary_index] = (secondary_index, similarity, -negative_distance)
    return matches


def align_pages(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    *,
    max_vertical_distance: float = 0.08,
) -> PageAlignment:
    if primary.page_index != secondary.page_index:
        raise ValueError("OCR results must describe the same page")

    matches = _match_primary_lines(primary, secondary, max_vertical_distance)
    remaining_secondary = set(range(len(secondary.lines))) - {
        secondary_index for secondary_index, _, _ in matches.values()
    }
    pairs: list[LineAlignment] = []

    for primary_index, primary_line in enumerate(primary.lines):
        match = matches.get(primary_index)
        if match is None:
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

        secondary_index, similarity, distance = match
        secondary_line = secondary.lines[secondary_index]
        pairs.append(
            LineAlignment(
                primary_line_index=primary_index,
                secondary_line_index=secondary_index,
                text_similarity=similarity,
                vertical_distance=distance,
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
