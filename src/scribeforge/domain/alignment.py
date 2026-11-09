from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from itertools import pairwise

from scribeforge.domain.direction import resolve_page_direction
from scribeforge.domain.normalize import similarity_for_comparison
from scribeforge.domain.ocr import BlockType, BoundingBox, OCRLine, PageDirection, PageOCRResult
from scribeforge.domain.reading_order import reading_order_key
from scribeforge.domain.text_assembly import assemble_group_text

#: A hard cap, not merely a performance hint.  Larger groups make both the DP and
#: the resulting evidence harder to explain; a larger region must be reviewed in
#: several bounded pieces instead of causing an unbounded combination search.
MAX_GROUP_SIZE_LIMIT = 4
_DEFAULT_GROUP_SIZE = 3
_UNMATCHED_SCORE = -1.5
_MULTI_GROUP_PENALTY = 0.25
_CONSOLIDATION_REWARD = 2.25
_MIN_MULTI_SIMILARITY = 0.70


class ConflictKind(str, Enum):
    SUBSTITUTION = "substitution"
    INSERTION = "insertion"
    DELETION = "deletion"
    PUNCTUATION = "punctuation"


class AlignmentStatus(str, Enum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"


class AmbiguityReason(str, Enum):
    CLOSE_SCORES = "close_scores"
    COLUMN_MAPPING = "column_mapping"


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
class AlignmentAlternative:
    """One uncommitted mapping in an ambiguous alignment region."""

    primary_line_indexes: tuple[int, ...]
    secondary_line_indexes: tuple[int, ...]
    score: float
    text_similarity: float
    cross_axis_distance: float

    def __post_init__(self) -> None:
        if not self.primary_line_indexes or not self.secondary_line_indexes:
            raise ValueError("an alignment alternative needs lines from both engines")
        _validate_indexes(self.primary_line_indexes, "primary")
        _validate_indexes(self.secondary_line_indexes, "secondary")


@dataclass(frozen=True, slots=True)
class AlignmentAmbiguity:
    """Why a region was not paired, with the competing evidence preserved."""

    reason: AmbiguityReason
    score_margin: float
    alternatives: tuple[AlignmentAlternative, ...]

    def __post_init__(self) -> None:
        if self.score_margin < 0:
            raise ValueError("ambiguity score margin must be non-negative")
        if len(self.alternatives) < 2:
            raise ValueError("an ambiguity needs at least two alternatives")


@dataclass(frozen=True, slots=True)
class LineAlignment:
    """A bounded group mapping, an unmatched line, or an ambiguous region.

    The two scalar indexes are the original v2-era fields and remain the first
    member of each group.  Consequently every old 1:1 record has exactly its old
    meaning.  New code must use the tuple fields, which contain *all* original
    OCR indexes in reading order.  No source text is stored or rewritten here.

    ``cross_axis_distance`` is the gap perpendicular to the reading direction:
    Y for horizontal pages and X for vertical pages.  ``vertical_distance`` is
    retained as a source-compatible alias.
    """

    primary_line_index: int | None
    secondary_line_index: int | None
    text_similarity: float
    cross_axis_distance: float | None
    conflicts: tuple[AlignmentConflict, ...]
    primary_line_indexes: tuple[int, ...] = ()
    secondary_line_indexes: tuple[int, ...] = ()
    status: AlignmentStatus = AlignmentStatus.MATCHED
    ambiguity: AlignmentAmbiguity | None = None

    def __post_init__(self) -> None:
        primary = self.primary_line_indexes
        secondary = self.secondary_line_indexes
        if not primary and self.primary_line_index is not None:
            primary = (self.primary_line_index,)
            object.__setattr__(self, "primary_line_indexes", primary)
        if not secondary and self.secondary_line_index is not None:
            secondary = (self.secondary_line_index,)
            object.__setattr__(self, "secondary_line_indexes", secondary)
        if primary and self.primary_line_index is None:
            object.__setattr__(self, "primary_line_index", primary[0])
        if secondary and self.secondary_line_index is None:
            object.__setattr__(self, "secondary_line_index", secondary[0])
        if primary and self.primary_line_index != primary[0]:
            raise ValueError("primary scalar index must be the first group member")
        if secondary and self.secondary_line_index != secondary[0]:
            raise ValueError("secondary scalar index must be the first group member")
        if primary:
            _validate_indexes(primary, "primary")
        if secondary:
            _validate_indexes(secondary, "secondary")
        if not primary and not secondary:
            raise ValueError("an alignment needs at least one original OCR line")
        if self.status is AlignmentStatus.MATCHED and (not primary or not secondary):
            object.__setattr__(self, "status", AlignmentStatus.UNMATCHED)
        if self.status is AlignmentStatus.UNMATCHED and primary and secondary:
            raise ValueError("an unmatched alignment cannot reference both engines")
        if self.status is AlignmentStatus.AMBIGUOUS:
            if not primary or not secondary or self.ambiguity is None:
                raise ValueError("an ambiguous alignment needs both sides and alternatives")
            primary_members = set(primary)
            secondary_members = set(secondary)
            if any(
                not set(alternative.primary_line_indexes).issubset(primary_members)
                or not set(alternative.secondary_line_indexes).issubset(secondary_members)
                for alternative in self.ambiguity.alternatives
            ):
                raise ValueError("ambiguity alternative references a line outside its region")
        elif self.ambiguity is not None:
            raise ValueError("ambiguity evidence requires ambiguous status")

    @property
    def vertical_distance(self) -> float | None:
        """Backwards-compatible alias for :attr:`cross_axis_distance`."""

        return self.cross_axis_distance

    @property
    def is_ambiguous(self) -> bool:
        return self.status is AlignmentStatus.AMBIGUOUS


@dataclass(frozen=True, slots=True)
class PageAlignment:
    page_index: int
    primary_engine: str
    secondary_engine: str
    pairs: tuple[LineAlignment, ...]
    direction: PageDirection = PageDirection.HORIZONTAL_LR

    @property
    def ambiguous_pairs(self) -> tuple[LineAlignment, ...]:
        return tuple(pair for pair in self.pairs if pair.is_ambiguous)


def _validate_indexes(indexes: tuple[int, ...], side: str) -> None:
    if any(index < 0 for index in indexes):
        raise ValueError(f"{side} line indexes must be non-negative")
    if len(set(indexes)) != len(indexes):
        raise ValueError(f"{side} line indexes must not repeat within a group")


def _center_x(line: OCRLine) -> float:
    return line.box.x + line.box.width / 2


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


def _union_boxes(lines: Sequence[OCRLine]) -> BoundingBox:
    if not lines:
        raise ValueError("cannot compute the box of an empty line group")
    left = min(line.box.x for line in lines)
    top = min(line.box.y for line in lines)
    right = max(line.box.x + line.box.width for line in lines)
    bottom = max(line.box.y + line.box.height for line in lines)
    return BoundingBox(left, top, right - left, bottom - top)


def _overlap_ratio(
    first_start: float, first_end: float, second_start: float, second_end: float
) -> float:
    overlap = max(0.0, min(first_end, second_end) - max(first_start, second_start))
    shortest = min(first_end - first_start, second_end - second_start)
    return overlap / shortest if shortest > 0 else 0.0


def _interval_gap(
    first_start: float, first_end: float, second_start: float, second_end: float
) -> float:
    return max(0.0, first_start - second_end, second_start - first_end)


def _box_cross_center(box: BoundingBox, direction: PageDirection) -> float:
    return box.x + box.width / 2 if direction.is_vertical else box.y + box.height / 2


def _group_line_indexes_into_columns(
    lines: Sequence[OCRLine], direction: PageDirection, column_gap: float = 0.05
) -> list[list[int]]:
    """Cluster physical page columns by X geometry for every page direction.

    Horizontal lines stack in Y inside a column; vertical lines stack in Y too,
    while adjacent vertical *columns* differ in X.  The old direction-dependent
    main-axis grouping used Y for vertical pages and therefore could not hold a
    split vertical column together.  X-overlap handles short centred lines, but
    a large centre gap always wins over a small accidental overlap: that is what
    keeps left/right columns apart when their boxes nearly touch.
    """

    del direction  # X is the physical column-separation axis in both writing modes.
    if not lines:
        return []
    order = sorted(range(len(lines)), key=lambda i: (_center_x(lines[i]), i))
    columns: list[list[int]] = [[order[0]]]
    for index in order[1:]:
        previous = columns[-1][-1]
        current_line = lines[index]
        previous_line = lines[previous]
        centre_gap = abs(_center_x(current_line) - _center_x(previous_line))
        x_overlap = _overlap_ratio(
            current_line.box.x,
            current_line.box.x + current_line.box.width,
            previous_line.box.x,
            previous_line.box.x + previous_line.box.width,
        )
        if (
            centre_gap <= column_gap
            or (centre_gap <= column_gap * 2.5 and x_overlap >= 0.75)
            or (centre_gap <= column_gap * 4.0 and x_overlap >= 0.90)
        ):
            columns[-1].append(index)
        else:
            columns.append([index])
    return columns


@dataclass(frozen=True, slots=True)
class _Candidate:
    primary_line_indexes: tuple[int, ...]
    secondary_line_indexes: tuple[int, ...]
    score: float
    text_similarity: float
    cross_axis_distance: float | None


@dataclass(frozen=True, slots=True)
class _DPEntry:
    score: float
    previous: tuple[int, int, int] | None
    candidate: _Candidate | None


@dataclass(frozen=True, slots=True)
class _AlignedPath:
    score: float
    candidates: tuple[_Candidate, ...]


@dataclass(frozen=True, slots=True)
class _GroupProfile:
    indexes: tuple[int, ...]
    box: BoundingBox
    text: str
    roles: frozenset[BlockType]
    internally_adjacent: bool


def _internally_adjacent(lines: Sequence[OCRLine], max_gap: float) -> bool:
    for first, second in pairwise(lines):
        gap = _interval_gap(
            first.box.y,
            first.box.y + first.box.height,
            second.box.y,
            second.box.y + second.box.height,
        )
        if gap > max_gap:
            return False
    return True


def _group_profile(
    result: PageOCRResult, indexes: tuple[int, ...], max_gap: float
) -> _GroupProfile:
    lines = [result.lines[index] for index in indexes]
    roles: set[BlockType] = set()
    for index in indexes:
        block = result.block_of_line(index)
        roles.add(BlockType.UNKNOWN if block is None else block.block_type)
    return _GroupProfile(
        indexes=indexes,
        box=_union_boxes(lines),
        text=assemble_group_text(line.text for line in lines),
        roles=frozenset(roles),
        internally_adjacent=_internally_adjacent(lines, max_gap),
    )


def _group_profiles(
    result: PageOCRResult,
    order: tuple[int, ...],
    max_group_size: int,
    max_gap: float,
) -> dict[tuple[int, int], _GroupProfile]:
    """Compute each side's bounded runs once, not once per opposing run."""

    return {
        (start, count): _group_profile(result, order[start : start + count], max_gap)
        for start in range(len(order))
        for count in range(1, max_group_size + 1)
        if start + count <= len(order)
    }


def _roles_allow_group(primary: _GroupProfile, secondary: _GroupProfile) -> bool:
    """Apply declared block roles without inventing labels for unknown evidence.

    Two wholly unknown sides may form a geometry-constrained multiline candidate.
    When exactly one side has labels, a multiline candidate is allowed only when
    that side is uniformly ``TEXT``: ordinary PP-OCR has no layout output, and a
    blanket asymmetric ban made the default MinerU + PaddleOCR path unable to
    align any split body line.  A known title/header/footer/footnote never gains
    this relaxation, mixed known/unknown roles still fail, and all geometric hard
    gates below remain unchanged.  Unknown lines are not relabelled as text.
    """

    primary_known = primary.roles - {BlockType.UNKNOWN}
    secondary_known = secondary.roles - {BlockType.UNKNOWN}
    if len(primary.roles) > 1 or len(secondary.roles) > 1:
        return False
    if len(primary_known) > 1 or len(secondary_known) > 1:
        return False
    if primary_known and secondary_known and primary_known != secondary_known:
        return False
    asymmetric_evidence = bool(primary_known) != bool(secondary_known)
    multiline = len(primary.indexes) > 1 or len(secondary.indexes) > 1
    if not asymmetric_evidence or not multiline:
        return True
    known = primary_known or secondary_known
    return known == {BlockType.TEXT}


def _make_candidate(
    primary_order: tuple[int, ...],
    secondary_order: tuple[int, ...],
    primary_profiles: dict[tuple[int, int], _GroupProfile],
    secondary_profiles: dict[tuple[int, int], _GroupProfile],
    i: int,
    j: int,
    primary_count: int,
    secondary_count: int,
    direction: PageDirection,
    max_cross_axis_distance: float,
) -> _Candidate | None:
    primary_indexes = primary_order[i : i + primary_count]
    secondary_indexes = secondary_order[j : j + secondary_count]
    if primary_count == 0 or secondary_count == 0:
        return _Candidate(primary_indexes, secondary_indexes, _UNMATCHED_SCORE, 0.0, None)
    return _matched_profiles(
        primary_profiles[i, primary_count],
        secondary_profiles[j, secondary_count],
        direction,
        max_cross_axis_distance,
    )


def _matched_candidate(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    primary_indexes: tuple[int, ...],
    secondary_indexes: tuple[int, ...],
    direction: PageDirection,
    max_cross_axis_distance: float,
) -> _Candidate | None:
    return _matched_profiles(
        _group_profile(primary, primary_indexes, max_cross_axis_distance),
        _group_profile(secondary, secondary_indexes, max_cross_axis_distance),
        direction,
        max_cross_axis_distance,
    )


def _matched_profiles(
    primary: _GroupProfile,
    secondary: _GroupProfile,
    direction: PageDirection,
    max_cross_axis_distance: float,
) -> _Candidate | None:
    if not _roles_allow_group(primary, secondary):
        return None
    if not primary.internally_adjacent or not secondary.internally_adjacent:
        return None
    distance = abs(
        _box_cross_center(primary.box, direction) - _box_cross_center(secondary.box, direction)
    )
    # Geometry is a hard gate and is deliberately checked before SequenceMatcher.
    if distance > max_cross_axis_distance:
        return None
    if direction.is_vertical:
        main_gap = _interval_gap(
            primary.box.y,
            primary.box.y + primary.box.height,
            secondary.box.y,
            secondary.box.y + secondary.box.height,
        )
    else:
        main_gap = _interval_gap(
            primary.box.x,
            primary.box.x + primary.box.width,
            secondary.box.x,
            secondary.box.x + secondary.box.width,
        )
    if main_gap > 0.05:
        return None

    similarity = similarity_for_comparison(primary.text, secondary.text)
    if (
        len(primary.indexes) > 1 or len(secondary.indexes) > 1
    ) and similarity < _MIN_MULTI_SIMILARITY:
        return None
    spatial_score = max(0.0, 1.0 - distance / max_cross_axis_distance)
    extra_boundaries = len(primary.indexes) + len(secondary.indexes) - 2
    group_penalty = _MULTI_GROUP_PENALTY * extra_boundaries
    # Without a coverage reward, two mediocre 1:1 matches receive the spatial
    # base score twice and can beat one exact 2:2 partition of the same text.
    consolidation_reward = _CONSOLIDATION_REWARD * similarity * extra_boundaries
    score = 4.0 * similarity + 0.5 * spatial_score - group_penalty + consolidation_reward
    return _Candidate(primary.indexes, secondary.indexes, score, similarity, distance)


def _entry_order(entry: _DPEntry) -> tuple[float, int, int, int, tuple[int, ...], tuple[int, ...]]:
    candidate = entry.candidate
    previous = entry.previous or (0, 0, 0)
    return (
        -entry.score,
        previous[2],
        previous[0],
        previous[1],
        () if candidate is None else candidate.primary_line_indexes,
        () if candidate is None else candidate.secondary_line_indexes,
    )


def _add_entry(target: list[_DPEntry | None], entry: _DPEntry) -> None:
    entries = [item for item in target if item is not None]
    entries.append(entry)
    entries.sort(key=_entry_order)
    # Two complete paths are enough to recognise a near tie.  Keeping only two at
    # every state bounds memory and still preserves the global runner-up.
    target[:] = [*entries[:2], *([None] * max(0, 2 - len(entries)))]


def _reconstruct(entry: _DPEntry, states: list[list[list[_DPEntry | None]]]) -> _AlignedPath:
    candidates: list[_Candidate] = []
    current = entry
    while current.previous is not None:
        if current.candidate is None:
            raise RuntimeError("alignment backpointer is missing its candidate")
        candidates.append(current.candidate)
        i, j, rank = current.previous
        previous = states[i][j][rank]
        if previous is None:
            raise RuntimeError("alignment backpointer references an empty state")
        current = previous
    candidates.reverse()
    return _AlignedPath(entry.score, tuple(candidates))


def _two_best_paths(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    primary_order: tuple[int, ...],
    secondary_order: tuple[int, ...],
    direction: PageDirection,
    max_cross_axis_distance: float,
    max_group_size: int,
) -> tuple[_AlignedPath, _AlignedPath | None]:
    """Constrained sequence alignment with bounded contiguous transitions.

    There are O(n*m) states and at most (K+1)^2-1 transitions per state.  Two
    backpointers per state preserve the best and runner-up complete paths without
    copying paths or storing an exponential set of group combinations.
    """

    n = len(primary_order)
    m = len(secondary_order)
    primary_profiles = _group_profiles(
        primary, primary_order, max_group_size, max_cross_axis_distance
    )
    secondary_profiles = _group_profiles(
        secondary, secondary_order, max_group_size, max_cross_axis_distance
    )
    states: list[list[list[_DPEntry | None]]] = [
        [[None, None] for _ in range(m + 1)] for _ in range(n + 1)
    ]
    states[0][0][0] = _DPEntry(0.0, None, None)

    for i in range(n + 1):
        for j in range(m + 1):
            # Entries at this state are final: every predecessor has smaller i/j.
            if all(previous is None for previous in states[i][j]):
                continue
            for primary_count in range(max_group_size + 1):
                if i + primary_count > n:
                    break
                for secondary_count in range(max_group_size + 1):
                    if j + secondary_count > m:
                        break
                    if primary_count == 0 and secondary_count == 0:
                        continue
                    if primary_count == 0 and secondary_count != 1:
                        continue
                    if secondary_count == 0 and primary_count != 1:
                        continue
                    candidate = _make_candidate(
                        primary_order,
                        secondary_order,
                        primary_profiles,
                        secondary_profiles,
                        i,
                        j,
                        primary_count,
                        secondary_count,
                        direction,
                        max_cross_axis_distance,
                    )
                    if candidate is None:
                        continue
                    for rank, previous in enumerate(states[i][j]):
                        if previous is None:
                            continue
                        entry = _DPEntry(
                            previous.score + candidate.score,
                            (i, j, rank),
                            candidate,
                        )
                        _add_entry(states[i + primary_count][j + secondary_count], entry)

    terminal = states[n][m]
    if terminal[0] is None:
        raise RuntimeError("unmatched transitions must make every sequence alignable")
    best = _reconstruct(terminal[0], states)
    second = None if terminal[1] is None else _reconstruct(terminal[1], states)
    return best, second


def _with_close_score_ambiguity(
    best: _AlignedPath,
    second: _AlignedPath | None,
    ambiguity_margin: float,
    primary_order: tuple[int, ...],
    secondary_order: tuple[int, ...],
) -> tuple[_Candidate | LineAlignment, ...]:
    if second is None or best.score - second.score > ambiguity_margin:
        return best.candidates
    best_candidates = set(best.candidates)
    second_candidates = set(second.candidates)
    changed = best_candidates ^ second_candidates
    alternatives = [
        candidate
        for candidate in changed
        if candidate.primary_line_indexes and candidate.secondary_line_indexes
    ]
    if len(alternatives) < 2:
        return best.candidates
    changed_primary = {index for candidate in changed for index in candidate.primary_line_indexes}
    changed_secondary = {
        index for candidate in changed for index in candidate.secondary_line_indexes
    }
    affected_primary = tuple(index for index in primary_order if index in changed_primary)
    affected_secondary = tuple(index for index in secondary_order if index in changed_secondary)
    if not affected_primary or not affected_secondary:
        return best.candidates
    alternatives.sort(
        key=lambda candidate: (
            -candidate.score,
            candidate.primary_line_indexes,
            candidate.secondary_line_indexes,
        )
    )
    ambiguity = AlignmentAmbiguity(
        AmbiguityReason.CLOSE_SCORES,
        max(0.0, best.score - second.score),
        tuple(
            AlignmentAlternative(
                candidate.primary_line_indexes,
                candidate.secondary_line_indexes,
                candidate.score,
                candidate.text_similarity,
                candidate.cross_axis_distance or 0.0,
            )
            for candidate in alternatives
        ),
    )
    leader = alternatives[0]
    ambiguous = LineAlignment(
        affected_primary[0],
        affected_secondary[0],
        leader.text_similarity,
        leader.cross_axis_distance,
        (),
        primary_line_indexes=affected_primary,
        secondary_line_indexes=affected_secondary,
        status=AlignmentStatus.AMBIGUOUS,
        ambiguity=ambiguity,
    )
    common = [candidate for candidate in best.candidates if candidate in second_candidates]
    return (*common, ambiguous)


def _improve_local_crossings(
    groups: tuple[_Candidate | LineAlignment, ...],
    primary: PageOCRResult,
    secondary: PageOCRResult,
    direction: PageDirection,
    max_cross_axis_distance: float,
    ambiguity_margin: float,
) -> tuple[_Candidate | LineAlignment, ...]:
    """Allow one strong 1:1 inversion inside overlapping two-line bands.

    Strict monotonic DP is right for printed reading order, but OCR engines can
    report two overlapping boxes in opposite orders.  A swap is considered only
    for adjacent 1:1 matches, only when both pairs of source boxes overlap in Y,
    and only when its total score beats the monotonic path by more than the
    ambiguity margin.  Otherwise the conservative DP result is retained.
    """

    if any(isinstance(group, LineAlignment) for group in groups):
        return groups
    candidates = [group for group in groups if isinstance(group, _Candidate)]
    result: list[_Candidate | LineAlignment] = []
    index = 0
    while index < len(candidates):
        if index + 1 >= len(candidates):
            result.append(candidates[index])
            break
        first = candidates[index]
        second = candidates[index + 1]
        if not (
            len(first.primary_line_indexes) == len(first.secondary_line_indexes) == 1
            and len(second.primary_line_indexes) == len(second.secondary_line_indexes) == 1
        ):
            result.append(first)
            index += 1
            continue
        primary_lines = [
            primary.lines[first.primary_line_indexes[0]],
            primary.lines[second.primary_line_indexes[0]],
        ]
        secondary_lines = [
            secondary.lines[first.secondary_line_indexes[0]],
            secondary.lines[second.secondary_line_indexes[0]],
        ]
        if not (
            _internally_adjacent(primary_lines, 0.0) and _internally_adjacent(secondary_lines, 0.0)
        ):
            result.append(first)
            index += 1
            continue
        crossed_first = _matched_candidate(
            primary,
            secondary,
            first.primary_line_indexes,
            second.secondary_line_indexes,
            direction,
            max_cross_axis_distance,
        )
        crossed_second = _matched_candidate(
            primary,
            secondary,
            second.primary_line_indexes,
            first.secondary_line_indexes,
            direction,
            max_cross_axis_distance,
        )
        improvement = float("-inf")
        if crossed_first is not None and crossed_second is not None:
            improvement = crossed_first.score + crossed_second.score - first.score - second.score
        if improvement > ambiguity_margin:
            assert crossed_first is not None and crossed_second is not None
            result.extend((crossed_first, crossed_second))
            index += 2
        else:
            result.append(first)
            index += 1
    return tuple(result)


def _column_score(primary_box: BoundingBox, secondary_box: BoundingBox) -> float | None:
    centre_gap = abs(
        primary_box.x + primary_box.width / 2 - secondary_box.x - secondary_box.width / 2
    )
    x_overlap = _overlap_ratio(
        primary_box.x,
        primary_box.x + primary_box.width,
        secondary_box.x,
        secondary_box.x + secondary_box.width,
    )
    # No zip-by-ordinal fallback: geometrically unrelated columns cannot pair.
    if centre_gap > 0.10 and x_overlap < 0.50:
        return None
    y_overlap = _overlap_ratio(
        primary_box.y,
        primary_box.y + primary_box.height,
        secondary_box.y,
        secondary_box.y + secondary_box.height,
    )
    return 3.0 * max(0.0, 1.0 - centre_gap / 0.20) + 2.0 * x_overlap + 0.5 * y_overlap


def _pair_columns(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    primary_columns: list[list[int]],
    secondary_columns: list[list[int]],
    direction: PageDirection,
) -> tuple[list[tuple[list[int], list[int]]], tuple[LineAlignment, ...]]:
    """Monotonic geometric assignment with explicit structural uncertainty.

    Near-equal alternatives are excluded from the deterministic column DP, but
    are no longer flattened into unrelated unmatched lines.  Their connected
    region becomes one AMBIGUOUS group with the competing mappings, scores and
    every original line index preserved.
    """

    if not primary_columns or not secondary_columns:
        return [], ()
    primary_boxes = [_union_boxes([primary.lines[i] for i in column]) for column in primary_columns]
    secondary_boxes = [
        _union_boxes([secondary.lines[i] for i in column]) for column in secondary_columns
    ]
    scores: dict[tuple[int, int], float] = {}
    for p, primary_box in enumerate(primary_boxes):
        for s, secondary_box in enumerate(secondary_boxes):
            score = _column_score(primary_box, secondary_box)
            if score is None:
                continue
            scores[p, s] = score
    near_edges: set[tuple[int, int]] = set()

    def add_near_edges(edges: list[tuple[int, int]]) -> None:
        if len(edges) < 2:
            return
        highest = max(scores[edge] for edge in edges)
        near_edges.update(edge for edge in edges if highest - scores[edge] <= 0.05)

    for p in range(len(primary_columns)):
        add_near_edges([(p, s) for s in range(len(secondary_columns)) if (p, s) in scores])
    for s in range(len(secondary_columns)):
        add_near_edges([(p, s) for p in range(len(primary_columns)) if (p, s) in scores])

    # A row and a column can participate in overlapping near-equal choices.  A
    # union-find keeps the whole connected region together instead of emitting
    # duplicate review pairs that cite the same original OCR line.
    parent: dict[tuple[str, int], tuple[str, int]] = {}
    for p, s in near_edges:
        parent.setdefault(("p", p), ("p", p))
        parent.setdefault(("s", s), ("s", s))

    def find(node: tuple[str, int]) -> tuple[str, int]:
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != node:
            following = parent[node]
            parent[node] = root
            node = following
        return root

    for p, s in near_edges:
        primary_root = find(("p", p))
        secondary_root = find(("s", s))
        if primary_root != secondary_root:
            parent[primary_root] = secondary_root

    components: dict[tuple[str, int], list[tuple[int, int]]] = {}
    for p, s in near_edges:
        components.setdefault(find(("p", p)), []).append((p, s))

    ambiguous_primary = {p for p, _s in near_edges}
    ambiguous_secondary = {s for _p, s in near_edges}
    geometric_scores = scores
    scores = {
        (p, s): score
        for (p, s), score in scores.items()
        if p not in ambiguous_primary and s not in ambiguous_secondary
    }

    structural_ambiguities: list[LineAlignment] = []
    for edges in components.values():
        candidates = sorted(set(edges))
        candidate_evidence: list[AlignmentAlternative] = []
        candidate_similarities: list[float] = []
        candidate_distances: list[float] = []
        for p, s in candidates:
            primary_indexes = tuple(
                sorted(
                    primary_columns[p],
                    key=lambda index: (
                        *reading_order_key(primary.lines[index], direction),
                        index,
                    ),
                )
            )
            secondary_indexes = tuple(
                sorted(
                    secondary_columns[s],
                    key=lambda index: (
                        *reading_order_key(secondary.lines[index], direction),
                        index,
                    ),
                )
            )
            primary_text = assemble_group_text(
                primary.lines[index].text for index in primary_indexes
            )
            secondary_text = assemble_group_text(
                secondary.lines[index].text for index in secondary_indexes
            )
            primary_box = _union_boxes([primary.lines[index] for index in primary_indexes])
            secondary_box = _union_boxes([secondary.lines[index] for index in secondary_indexes])
            distance = abs(
                _box_cross_center(primary_box, direction)
                - _box_cross_center(secondary_box, direction)
            )
            score = geometric_scores[p, s]
            similarity = similarity_for_comparison(primary_text, secondary_text)
            candidate_similarities.append(similarity)
            candidate_distances.append(distance)
            candidate_evidence.append(
                AlignmentAlternative(
                    primary_indexes,
                    secondary_indexes,
                    score,
                    similarity,
                    distance,
                )
            )
        if len(candidate_evidence) < 2:
            continue
        order = sorted(
            range(len(candidate_evidence)),
            key=lambda index: (
                -candidate_evidence[index].score,
                candidate_evidence[index].primary_line_indexes,
                candidate_evidence[index].secondary_line_indexes,
            ),
        )
        alternatives = tuple(candidate_evidence[index] for index in order)
        component_primary = {index for edge in candidates for index in primary_columns[edge[0]]}
        component_secondary = {index for edge in candidates for index in secondary_columns[edge[1]]}
        affected_primary = tuple(
            sorted(
                component_primary,
                key=lambda index: (*reading_order_key(primary.lines[index], direction), index),
            )
        )
        affected_secondary = tuple(
            sorted(
                component_secondary,
                key=lambda index: (*reading_order_key(secondary.lines[index], direction), index),
            )
        )
        leader = order[0]
        structural_ambiguities.append(
            LineAlignment(
                affected_primary[0],
                affected_secondary[0],
                candidate_similarities[leader],
                candidate_distances[leader],
                (),
                primary_line_indexes=affected_primary,
                secondary_line_indexes=affected_secondary,
                status=AlignmentStatus.AMBIGUOUS,
                ambiguity=AlignmentAmbiguity(
                    AmbiguityReason.COLUMN_MAPPING,
                    max(0.0, alternatives[0].score - alternatives[1].score),
                    alternatives,
                ),
            )
        )

    n = len(primary_columns)
    m = len(secondary_columns)
    values = [[float("-inf") for _ in range(m + 1)] for _ in range(n + 1)]
    moves: list[list[tuple[int, int, str] | None]] = [
        [None for _ in range(m + 1)] for _ in range(n + 1)
    ]
    values[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            options: list[tuple[float, int, tuple[int, int, str]]] = []
            if i and j and (i - 1, j - 1) in scores and values[i - 1][j - 1] != float("-inf"):
                options.append(
                    (values[i - 1][j - 1] + scores[i - 1, j - 1], 2, (i - 1, j - 1, "pair"))
                )
            if i and values[i - 1][j] != float("-inf"):
                options.append((values[i - 1][j] - 2.5, 1, (i - 1, j, "primary")))
            if j and values[i][j - 1] != float("-inf"):
                options.append((values[i][j - 1] - 2.5, 0, (i, j - 1, "secondary")))
            if not options:
                continue
            score, _preference, best_move = max(options, key=lambda option: (option[0], option[1]))
            values[i][j] = score
            moves[i][j] = best_move

    pairs: list[tuple[list[int], list[int]]] = []
    i, j = n, m
    while i or j:
        back_move = moves[i][j]
        if back_move is None:
            raise RuntimeError("column alignment lost a reachable backpointer")
        previous_i, previous_j, kind = back_move
        if kind == "pair":
            pairs.append((primary_columns[i - 1], secondary_columns[j - 1]))
        i, j = previous_i, previous_j
    pairs.reverse()
    return pairs, tuple(structural_ambiguities)


def _line_alignment(
    candidate: _Candidate,
    primary: PageOCRResult,
    secondary: PageOCRResult,
) -> LineAlignment:
    primary_indexes = candidate.primary_line_indexes
    secondary_indexes = candidate.secondary_line_indexes
    primary_text = assemble_group_text(primary.lines[index].text for index in primary_indexes)
    secondary_text = assemble_group_text(secondary.lines[index].text for index in secondary_indexes)
    if primary_indexes and secondary_indexes:
        conflicts = _diff_text(primary_text, secondary_text)
    elif primary_indexes:
        conflicts = (
            AlignmentConflict(
                ConflictKind.DELETION,
                primary_text,
                "",
                0,
                len(primary_text),
                0,
                0,
            ),
        )
    else:
        conflicts = (
            AlignmentConflict(
                ConflictKind.INSERTION,
                "",
                secondary_text,
                0,
                0,
                0,
                len(secondary_text),
            ),
        )
    return LineAlignment(
        primary_indexes[0] if primary_indexes else None,
        secondary_indexes[0] if secondary_indexes else None,
        candidate.text_similarity,
        candidate.cross_axis_distance,
        conflicts,
        primary_line_indexes=primary_indexes,
        secondary_line_indexes=secondary_indexes,
    )


def align_pages(
    primary: PageOCRResult,
    secondary: PageOCRResult,
    *,
    max_cross_axis_distance: float = 0.08,
    direction: PageDirection | None = None,
    max_group_size: int = _DEFAULT_GROUP_SIZE,
    ambiguity_margin: float = 0.20,
) -> PageAlignment:
    """Align two OCR pages with bounded groups and explicit ambiguity.

    Candidates are contiguous runs inside geometrically paired physical columns.
    Declared block roles, internal adjacency, cross-axis distance and main-axis
    overlap are hard constraints; text similarity is only one component of the
    score.  The DP maximises the complete column path rather than greedily taking
    the locally best pair.  If its best and runner-up paths differ by at most
    ``ambiguity_margin``, the affected lines become an AMBIGUOUS evidence group.

    For each paired column with n/m lines and group cap K, time is O(n*m*K^2)
    (plus sequence-comparison cost) and space is O(n*m); column assignment adds
    O(Cp*Cs).  K is limited to four even when a caller requests a larger value.
    """

    if primary.page_index != secondary.page_index:
        raise ValueError("OCR results must describe the same page")
    if max_cross_axis_distance <= 0:
        raise ValueError("cross-axis distance threshold must be positive")
    if not 1 <= max_group_size <= MAX_GROUP_SIZE_LIMIT:
        raise ValueError(f"max_group_size must be between 1 and {MAX_GROUP_SIZE_LIMIT}")
    if ambiguity_margin < 0:
        raise ValueError("ambiguity margin must be non-negative")

    page_direction = (
        direction if direction is not None else resolve_page_direction(primary, secondary)
    )
    primary_columns = _group_line_indexes_into_columns(primary.lines, page_direction)
    secondary_columns = _group_line_indexes_into_columns(secondary.lines, page_direction)
    paired_columns, column_ambiguities = _pair_columns(
        primary, secondary, primary_columns, secondary_columns, page_direction
    )

    groups: list[_Candidate | LineAlignment] = list(column_ambiguities)
    covered_primary: set[int] = {
        index for pair in column_ambiguities for index in pair.primary_line_indexes
    }
    covered_secondary: set[int] = {
        index for pair in column_ambiguities for index in pair.secondary_line_indexes
    }
    for primary_column, secondary_column in paired_columns:
        primary_order = tuple(
            sorted(
                primary_column,
                key=lambda index: (*reading_order_key(primary.lines[index], page_direction), index),
            )
        )
        secondary_order = tuple(
            sorted(
                secondary_column,
                key=lambda index: (
                    *reading_order_key(secondary.lines[index], page_direction),
                    index,
                ),
            )
        )
        best, second = _two_best_paths(
            primary,
            secondary,
            primary_order,
            secondary_order,
            page_direction,
            max_cross_axis_distance,
            max_group_size,
        )
        column_groups = _with_close_score_ambiguity(
            best, second, ambiguity_margin, primary_order, secondary_order
        )
        column_groups = _improve_local_crossings(
            column_groups,
            primary,
            secondary,
            page_direction,
            max_cross_axis_distance,
            ambiguity_margin,
        )
        groups.extend(column_groups)
        covered_primary.update(primary_column)
        covered_secondary.update(secondary_column)

    # Unpaired columns remain unmatched; ambiguous columns already carry their
    # complete region and alternatives above and must not be duplicated here.
    for index in range(len(primary.lines)):
        if index not in covered_primary:
            groups.append(_Candidate((index,), (), _UNMATCHED_SCORE, 0.0, None))
    for index in range(len(secondary.lines)):
        if index not in covered_secondary:
            groups.append(_Candidate((), (index,), _UNMATCHED_SCORE, 0.0, None))

    pairs = [
        group if isinstance(group, LineAlignment) else _line_alignment(group, primary, secondary)
        for group in groups
    ]
    order = sorted(
        range(len(pairs)),
        key=lambda index: (
            *reading_order_key(
                _pair_reference_line(pairs[index], primary, secondary), page_direction
            ),
            pairs[index].primary_line_indexes,
            pairs[index].secondary_line_indexes,
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
    """Synthetic union used only for sorting; source lines remain untouched."""

    lines = [primary.lines[index] for index in pair.primary_line_indexes]
    lines.extend(secondary.lines[index] for index in pair.secondary_line_indexes)
    return OCRLine("", _union_boxes(lines))
