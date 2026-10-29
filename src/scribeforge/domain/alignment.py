from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum

from scribeforge.domain.direction import resolve_page_direction
from scribeforge.domain.normalize import similarity_for_comparison
from scribeforge.domain.ocr import OCRLine, PageDirection, PageOCRResult
from scribeforge.domain.reading_order import reading_order_key


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
    """One aligned pair, or one unmatched line from either engine.

    ``cross_axis_distance`` is the gap along the axis *perpendicular* to the
    reading direction. For a horizontal page that is the vertical (Y) gap, which
    is why it was originally called ``vertical_distance``; for a vertical page it
    is the horizontal gap between columns. It is the same idea either way: how far
    apart two lines are across the reading axis.
    """

    primary_line_index: int | None
    secondary_line_index: int | None
    text_similarity: float
    cross_axis_distance: float | None
    conflicts: tuple[AlignmentConflict, ...]

    @property
    def vertical_distance(self) -> float | None:
        """Backwards-compatible alias for :attr:`cross_axis_distance`."""

        return self.cross_axis_distance


@dataclass(frozen=True, slots=True)
class PageAlignment:
    page_index: int
    primary_engine: str
    secondary_engine: str
    pairs: tuple[LineAlignment, ...]
    direction: PageDirection = PageDirection.HORIZONTAL_LR


def _center_y(line: OCRLine) -> float:
    return line.box.y + line.box.height / 2


def _center_x(line: OCRLine) -> float:
    return line.box.x + line.box.width / 2


def _cross_axis_center(line: OCRLine, direction: PageDirection) -> float:
    """Centre of a line along the axis used to pair lines with each other.

    Horizontal pages pair lines by their vertical position; vertical pages pair
    columns by their horizontal position. Getting this backwards on a vertical
    page merges every column into its neighbours and scatters the ones it does not
    merge, which is why the direction has to reach this function rather than being
    assumed.
    """

    return _center_x(line) if direction.is_vertical else _center_y(line)


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


def _text_similarity(primary: OCRLine, secondary: OCRLine) -> float:
    """Line similarity after glyph-variant folding.

    Without folding, an engine pair that disagrees only on 繁/简 or on a variant
    form scores as a poor match, and lines get paired by vertical distance alone.
    The similarity is used for *pairing* only; the compared text itself is never
    rewritten.
    """

    return similarity_for_comparison(primary.text, secondary.text)


def _main_axis_center(line: OCRLine, direction: PageDirection) -> float:
    """Centre of a line along the axis that separates columns.

    Horizontal pages are split into columns by their horizontal (X) position;
    vertical pages by their vertical (Y) position. Two lines in different
    columns must never be paired even if they sit at the same cross-axis
    position.
    """

    return _center_x(line) if not direction.is_vertical else _center_y(line)


def _group_line_indexes_into_columns(
    lines: Sequence[OCRLine], direction: PageDirection, column_gap: float = 0.05
) -> list[list[int]]:
    """Group line indexes into columns by their main-axis position.

    Lines are sorted by main-axis centre; adjacent lines closer than
    ``column_gap`` (a fraction of page width/height) belong to the same
    column. Lines in different columns are matched independently so a
    two-column page never pairs a left-column line with a right-column line.
    """

    if not lines:
        return []

    order = sorted(range(len(lines)), key=lambda i: _main_axis_center(lines[i], direction))
    columns: list[list[int]] = [[order[0]]]
    for index in order[1:]:
        previous = columns[-1][-1]
        gap = abs(
            _main_axis_center(lines[index], direction)
            - _main_axis_center(lines[previous], direction)
        )
        if gap <= column_gap:
            columns[-1].append(index)
        else:
            columns.append([index])
    return columns


def _match_primary_lines(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    max_cross_axis_distance: float,
    direction: PageDirection,
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

    Matching is performed per column: lines are first grouped by their
    main-axis position (X for horizontal pages, Y for vertical pages), and
    each column is matched independently. This keeps a two-column page from
    pairing a left-column line with a right-column line that merely happens to
    sit at the same cross-axis position.
    """

    matches: dict[int, tuple[int, float, float]] = {}
    primary_columns = _group_line_indexes_into_columns(primary.lines, direction)
    secondary_columns = _group_line_indexes_into_columns(secondary.lines, direction)

    # Pair columns by their main-axis position: the leftmost primary column
    # with the leftmost secondary column, and so on. A page whose column count
    # differs between engines still matches every column that exists on both
    # sides; unmatched columns fall through to unpaired lines below.
    paired_columns = list(zip(primary_columns, secondary_columns))

    for p_col, s_col in paired_columns:
        secondary_centers = [
            _cross_axis_center(secondary.lines[i], direction) for i in s_col
        ]
        candidates: list[tuple[float, float, int, int]] = []
        for primary_index in p_col:
            primary_line = primary.lines[primary_index]
            primary_center = _cross_axis_center(primary_line, direction)
            for offset, secondary_index in enumerate(s_col):
                distance = abs(primary_center - secondary_centers[offset])
                if distance > max_cross_axis_distance:
                    continue
                similarity = _text_similarity(
                    primary_line, secondary.lines[secondary_index]
                )
                candidates.append(
                    (similarity, -distance, primary_index, secondary_index)
                )

        candidates.sort(reverse=True)

        assigned_primary: set[int] = set()
        assigned_secondary: set[int] = set()
        for similarity, negative_distance, primary_index, secondary_index in candidates:
            if primary_index in assigned_primary or secondary_index in assigned_secondary:
                continue
            assigned_primary.add(primary_index)
            assigned_secondary.add(secondary_index)
            matches[primary_index] = (
                secondary_index,
                similarity,
                -negative_distance,
            )

    return matches


def align_pages(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    *,
    max_cross_axis_distance: float = 0.08,
    direction: PageDirection | None = None,
) -> PageAlignment:
    """Align two engine results for one page.

    ``direction`` may be supplied by the caller (for example from page-level
    evidence); otherwise it is resolved from what the engines declared and, failing
    that, from line geometry.
    """

    if primary.page_index != secondary.page_index:
        raise ValueError("OCR results must describe the same page")
    if max_cross_axis_distance <= 0:
        raise ValueError("cross-axis distance threshold must be positive")

    page_direction = (
        direction
        if direction is not None
        else resolve_page_direction(primary, secondary)
    )
    matches = _match_primary_lines(
        primary, secondary, max_cross_axis_distance, page_direction
    )
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
                    cross_axis_distance=None,
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
                cross_axis_distance=distance,
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
                cross_axis_distance=None,
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

    # Emit pairs in reading order. Pair indexes are part of the persisted
    # evidence (decisions reference them), so ordering has to be decided here,
    # once, rather than left to whatever order the engine happened to return.
    order = sorted(
        range(len(pairs)),
        key=lambda index: reading_order_key(
            _pair_reference_line(pairs[index], primary, secondary), page_direction
        ),
    )
    return PageAlignment(
        page_index=primary.page_index,
        primary_engine=primary.engine,
        secondary_engine=secondary.engine,
        pairs=tuple(pairs[index] for index in order),
        direction=page_direction,
    )


def _pair_reference_line(
    pair: LineAlignment,
    primary: PageOCRResult,
    secondary: PageOCRResult,
) -> OCRLine:
    """The line whose position decides where a pair sits in reading order."""

    if pair.primary_line_index is not None:
        return primary.lines[pair.primary_line_index]
    assert pair.secondary_line_index is not None
    return secondary.lines[pair.secondary_line_index]
