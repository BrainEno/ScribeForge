"""Experimental, review-only multi-line alignment proposals.

This module does not feed accepted decisions, persistence, or export. Its output
must not be treated as a verification or as authority to accept OCR text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from scribeforge.domain.normalize import similarity_for_comparison
from scribeforge.domain.ocr import BlockType, BoundingBox, PageDirection, PageOCRResult
from scribeforge.domain.reading_order import reading_order_key

Status = Literal["proposed", "unmatched", "ambiguous"]
_MAX_CELLS = 250_000
_TIE_MARGIN = 0.03


@dataclass(frozen=True, slots=True)
class ProposedGroup:
    primary_indexes: tuple[int, ...]
    secondary_indexes: tuple[int, ...]
    status: Status
    score: float | None = None


@dataclass(frozen=True, slots=True)
class GroupProposals:
    page_index: int
    direction: PageDirection
    groups: tuple[ProposedGroup, ...]


def _bounds(result: PageOCRResult, indexes: tuple[int, ...]) -> tuple[float, float, float, float]:
    boxes = [result.lines[i].box for i in indexes]
    return (min(b.x for b in boxes), min(b.y for b in boxes),
            max(b.x + b.width for b in boxes), max(b.y + b.height for b in boxes))


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    maximum = max(a1 - a0, b1 - b0)
    return 0.0 if maximum <= 0 else max(0.0, min(a1, b1) - max(a0, b0)) / maximum


def _line_blocks(result: PageOCRResult) -> list[set[int]]:
    memberships: list[set[int]] = [set() for _ in result.lines]
    for block_index, block in enumerate(result.blocks):
        for index in range(block.line_start, block.line_end):
            memberships[index].add(block_index)
    return memberships


def _contiguous(a: BoundingBox, b: BoundingBox, direction: PageDirection) -> bool:
    if direction.is_vertical:
        cross = _overlap(a.x, a.x + a.width, b.x, b.x + b.width)
        gap = max(0.0, max(a.y, b.y) - min(a.y + a.height, b.y + b.height))
    else:
        cross = _overlap(a.y, a.y + a.height, b.y, b.y + b.height)
        gap = max(0.0, max(a.x, b.x) - min(a.x + a.width, b.x + b.width))
    # Overlapping fragments may be duplicates rather than a split; do not join them.
    main_overlap = (_overlap(a.y, a.y + a.height, b.y, b.y + b.height)
                    if direction.is_vertical else
                    _overlap(a.x, a.x + a.width, b.x, b.x + b.width))
    return cross >= 0.5 and gap <= 0.04 and main_overlap < 0.1


def _candidate(primary: PageOCRResult, secondary: PageOCRResult,
               left: tuple[int, ...], right: tuple[int, ...], direction: PageDirection,
               primary_blocks: list[set[int]], secondary_blocks: list[set[int]]) -> float | None:
    for result, indexes, memberships in ((primary, left, primary_blocks),
                                          (secondary, right, secondary_blocks)):
        if any(len(memberships[i]) > 1 for i in indexes):
            return None
        if len(indexes) == 2:
            if not _contiguous(result.lines[indexes[0]].box, result.lines[indexes[1]].box, direction):
                return None
            if result.blocks and (not memberships[indexes[0]] or
                                  memberships[indexes[0]] != memberships[indexes[1]]):
                return None
    left_roles = {primary.blocks[b].block_type for i in left for b in primary_blocks[i]}
    right_roles = {secondary.blocks[b].block_type for i in right for b in secondary_blocks[i]}
    if len(left_roles) > 1 or len(right_roles) > 1 or BlockType.UNKNOWN in left_roles | right_roles:
        return None
    if left_roles and right_roles and left_roles != right_roles:
        return None
    ax, ay, ar, ab = _bounds(primary, left)
    bx, by, br, bb = _bounds(secondary, right)
    x_overlap, y_overlap = _overlap(ax, ar, bx, br), _overlap(ay, ab, by, bb)
    if min(x_overlap, y_overlap) < 0.55:
        return None
    similarity = similarity_for_comparison(
        "".join(primary.lines[i].text for i in left),
        "".join(secondary.lines[i].text for i in right),
    )
    if similarity < 0.65:
        return None
    return 0.75 * similarity + 0.25 * min(x_overlap, y_overlap)


def propose_group_alignment(primary: PageOCRResult, secondary: PageOCRResult, *,
                            direction: PageDirection) -> GroupProposals:
    """Propose bounded 1:1, 1:2, 2:1 groups; never approve or persist them.

    At most three group transitions are examined per cell. Sorting competing
    local candidates costs O(P*S*log(P+S)); memory is O(P*S), with additional
    block-indexing and text-comparison costs. Local and whole-path near ties are
    reported as ambiguous singleton evidence rather than selecting one. A hard cell limit
    fails closed. These are proposals, not accepted text or a validated layout.
    """
    if primary.page_index != secondary.page_index:
        raise ValueError("OCR results must describe the same page")
    n, m = len(primary.lines), len(secondary.lines)
    if (n + 1) * (m + 1) > _MAX_CELLS:
        raise ValueError("group alignment exceeds the review-only cell limit")
    order_p = tuple(sorted(range(n), key=lambda i: reading_order_key(primary.lines[i], direction)))
    order_s = tuple(sorted(range(m), key=lambda i: reading_order_key(secondary.lines[i], direction)))
    primary_blocks, secondary_blocks = _line_blocks(primary), _line_blocks(secondary)
    candidates: dict[tuple[int, int, int, int], float] = {}
    for i in range(n):
        for j in range(m):
            for p, s in ((1, 1), (1, 2), (2, 1)):
                if i + p <= n and j + s <= m:
                    score = _candidate(primary, secondary, order_p[i:i+p], order_s[j:j+s], direction,
                                       primary_blocks, secondary_blocks)
                    if score is not None:
                        candidates[(i, j, p, s)] = score
    # Flag all evidence involved in near-equal overlapping alternatives, not just
    # the arbitrary winner. These lines cannot participate in proposed matches.
    ambiguous_p: set[int] = set()
    ambiguous_s: set[int] = set()
    by_line: dict[tuple[str, int], list[tuple[tuple[int, int, int, int], float]]] = {}
    for key, score in candidates.items():
        i, j, p, s = key
        for item in [("p", k) for k in range(i, i+p)] + [("s", k) for k in range(j, j+s)]:
            by_line.setdefault(item, []).append((key, score))
    for options in by_line.values():
        options.sort(key=lambda item: (-item[1], item[0]))
        if len(options) > 1 and options[0][1] - options[1][1] < _TIE_MARGIN:
            for (i, j, p, s), score in options:
                if options[0][1] - score >= _TIE_MARGIN:
                    break
                ambiguous_p.update(range(i, i+p))
                ambiguous_s.update(range(j, j+s))
    def transitions(i: int, j: int) -> list[tuple[float, int, int]]:
        options: list[tuple[float, int, int]] = []
        if i < n:
            options.append((0.0, 1, 0))
        if j < m:
            options.append((0.0, 0, 1))
        for p, q in ((1, 1), (1, 2), (2, 1)):
            score = candidates.get((i, j, p, q))
            if score is not None and not (set(range(i, i+p)) & ambiguous_p or
                                           set(range(j, j+q)) & ambiguous_s):
                options.append((score, p, q))
        return options

    def solve() -> tuple[list[list[float]], dict[tuple[int, int], tuple[int, int]]]:
        costs = [[0.0] * (m + 1) for _ in range(n + 1)]
        steps: dict[tuple[int, int], tuple[int, int]] = {}
        for i in range(n, -1, -1):
            for j in range(m, -1, -1):
                if i == n and j == m:
                    continue
                options = [(score + costs[i+p][j+q], p, q)
                           for score, p, q in transitions(i, j)]
                value, p, q = max(options, key=lambda option: (option[0], option[1]+option[2], option[1]))
                costs[i][j] = value
                steps[(i, j)] = (p, q)
        return costs, steps

    costs, steps = solve()
    # Check whole-path alternatives, not only local candidate scores. Forward and
    # backward optima establish whether each transition belongs to a path within
    # the ambiguity margin of the optimum; different assignments for the same
    # line make its competing groups unsafe to choose automatically.
    forward = [[float("-inf")] * (m + 1) for _ in range(n + 1)]
    forward[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            for score, p, q in transitions(i, j):
                forward[i+p][j+q] = max(forward[i+p][j+q], forward[i][j] + score)
    alternatives: dict[tuple[str, int], set[tuple[int, int, int, int] | None]] = {}
    for i in range(n + 1):
        for j in range(m + 1):
            for score, p, q in transitions(i, j):
                if forward[i][j] + score + costs[i+p][j+q] < costs[0][0] - _TIE_MARGIN:
                    continue
                transition_key = (i, j, p, q) if p and q else None
                for item in [("p", k) for k in range(i, i+p)] + [("s", k) for k in range(j, j+q)]:
                    alternatives.setdefault(item, set()).add(transition_key)
    for (side, index), choices in alternatives.items():
        if len(choices) <= 1:
            continue
        (ambiguous_p if side == "p" else ambiguous_s).add(index)
        for choice in choices:
            if choice is not None:
                i, j, p, q = choice
                ambiguous_p.update(range(i, i+p))
                ambiguous_s.update(range(j, j+q))
    if ambiguous_p or ambiguous_s:
        costs, steps = solve()
    groups: list[ProposedGroup] = []
    i = j = 0
    while i < n or j < m:
        p, s = steps[(i, j)]
        status: Status = "proposed" if p and s else (
            "ambiguous" if (p and i in ambiguous_p) or (s and j in ambiguous_s) else "unmatched"
        )
        groups.append(ProposedGroup(order_p[i:i+p], order_s[j:j+s], status,
                                    candidates.get((i, j, p, s)) if p and s else None))
        i, j = i + p, j + s
    return GroupProposals(primary.page_index, direction, tuple(groups))
