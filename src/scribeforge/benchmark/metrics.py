from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for i, expected in enumerate(reference, start=1):
        current = [i]
        for j, actual in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[j] + 1,
                    previous[j - 1] + (expected != actual),
                )
            )
        previous = current
    return previous[-1]


def scoring_text(text: str) -> str:
    """Canonical form of text before scoring.

    A ground-truth file conventionally ends with a newline, while a hypothesis
    built by joining lines does not; counting that difference as an inserted
    character adds one fake error to every page and inflates low CER figures by
    a relative amount that matters (0.5% -> 0.6%). Trailing whitespace inside a
    line is layout, not content, so it is trimmed for the same reason. Blank lines
    *inside* a page are preserved because they delimit reading order structure;
    blank lines at either end of a document are file formatting.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def error_rate_from_counts(edit_count: int, reference_units: int) -> float:
    """Apply the benchmark's zero-reference convention to raw edit counts."""

    if edit_count < 0 or reference_units < 0:
        raise ValueError("edit and reference counts must be non-negative")
    if reference_units == 0:
        return 0.0 if edit_count == 0 else 1.0
    return edit_count / reference_units


def _error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    return error_rate_from_counts(_edit_distance(reference, hypothesis), len(reference))


def character_error_counts(reference: str, hypothesis: str) -> tuple[int, int]:
    return _edit_distance(tuple(reference), tuple(hypothesis)), len(reference)


def character_error_rate(reference: str, hypothesis: str) -> float:
    return error_rate_from_counts(*character_error_counts(reference, hypothesis))


def word_tokenizer_for_language(language: str) -> str:
    """Return the versioned word tokenizer, or an explicit N/A marker.

    ``whitespace-v1`` preserves case and punctuation and splits only on Unicode
    whitespace via :meth:`str.split`. Chinese, Japanese and unknown languages
    have no implicit word segmenter: emitting a whitespace-based "WER" for them
    would compare implementation artifacts rather than recognition quality.
    """

    primary_language = language.strip().lower().replace("_", "-").split("-", 1)[0]
    if primary_language in {"zh", "cmn", "yue", "lzh", "ja", "und", ""}:
        return "not-applicable"
    return "whitespace-v1"


def word_error_counts(
    reference: str, hypothesis: str, *, language: str = "en"
) -> tuple[int, int] | None:
    if word_tokenizer_for_language(language) == "not-applicable":
        return None
    reference_words = tuple(reference.split())
    hypothesis_words = tuple(hypothesis.split())
    return _edit_distance(reference_words, hypothesis_words), len(reference_words)


def word_error_rate(
    reference: str, hypothesis: str, *, language: str = "en"
) -> float | None:
    counts = word_error_counts(reference, hypothesis, language=language)
    return None if counts is None else error_rate_from_counts(*counts)


def _is_punctuation(character: str) -> bool:
    return unicodedata.category(character).startswith("P")


def punctuation_accuracy(reference: str, hypothesis: str) -> float:
    expected = tuple(character for character in reference if _is_punctuation(character))
    actual = tuple(character for character in hypothesis if _is_punctuation(character))
    if not expected:
        return 1.0 if not actual else 0.0
    return max(0.0, 1.0 - _edit_distance(expected, actual) / len(expected))


def reading_order_errors(
    reference: Sequence[str], hypothesis: Sequence[str]
) -> int:
    """Count pairwise order inversions among exactly matched lines.

    Unmatched lines are excluded: omissions and insertions are measured
    separately by line_error_counts. Repeated lines map to successive
    reference occurrences so duplicates do not invent inversions.
    """
    positions: dict[str, list[int]] = {}
    for index, line in enumerate(reference):
        positions.setdefault(line, []).append(index)

    used: dict[str, int] = {}
    matched_positions: list[int] = []
    for line in hypothesis:
        candidates = positions.get(line, [])
        offset = used.get(line, 0)
        if offset < len(candidates):
            matched_positions.append(candidates[offset])
            used[line] = offset + 1

    inversions = 0
    for index, position in enumerate(matched_positions):
        inversions += sum(earlier > position for earlier in matched_positions[:index])
    return inversions


@dataclass(frozen=True, slots=True)
class LineErrorMetrics:
    """Ordered text-line differences; no geometric alignment is implied."""

    missing_lines: int
    extra_lines: int
    changed_lines: int

    @property
    def net_missing_lines(self) -> int:
        return self.missing_lines - self.changed_lines

    @property
    def net_extra_lines(self) -> int:
        return self.extra_lines - self.changed_lines


def _similar_line_pairs(
    reference: Sequence[str], hypothesis: Sequence[str]
) -> list[tuple[int, int]]:
    """Greedily pair unmatched lines with character similarity of at least 0.5."""

    candidates = [
        (
            SequenceMatcher(None, expected, actual, autojunk=False).ratio(),
            reference_index,
            hypothesis_index,
        )
        for reference_index, expected in enumerate(reference)
        for hypothesis_index, actual in enumerate(hypothesis)
    ]
    used_reference: set[int] = set()
    used_hypothesis: set[int] = set()
    pairs: list[tuple[int, int]] = []
    ordered_candidates = sorted(candidates, key=lambda item: (-item[0], item[1], item[2]))
    for ratio, reference_index, hypothesis_index in ordered_candidates:
        if ratio < 0.5:
            break
        if reference_index in used_reference or hypothesis_index in used_hypothesis:
            continue
        used_reference.add(reference_index)
        used_hypothesis.add(hypothesis_index)
        pairs.append((reference_index, hypothesis_index))
    return pairs


def line_error_metrics(
    reference: Sequence[str], hypothesis: Sequence[str]
) -> LineErrorMetrics:
    """Preserve strict LCS counts and add clearly-scoped changed-line evidence.

    Exact LCS unmatched lines are considered for one-to-one pairing by
    ``SequenceMatcher.ratio >= 0.5``. This is deterministic textual similarity,
    not spatial alignment and not proof of a physical missing text region.
    """

    rows, cols = len(reference), len(hypothesis)
    table = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows):
        for j in range(cols):
            if reference[i] == hypothesis[j]:
                table[i + 1][j + 1] = table[i][j] + 1
            else:
                table[i + 1][j + 1] = max(table[i][j + 1], table[i + 1][j])

    unmatched_reference: list[str] = []
    unmatched_hypothesis: list[str] = []
    i, j = rows, cols
    while i or j:
        if i and j and reference[i - 1] == hypothesis[j - 1] and table[i][j] == table[i - 1][j - 1] + 1:
            i -= 1
            j -= 1
        elif j and (not i or table[i][j - 1] >= table[i - 1][j]):
            unmatched_hypothesis.append(hypothesis[j - 1])
            j -= 1
        else:
            unmatched_reference.append(reference[i - 1])
            i -= 1

    changed = len(_similar_line_pairs(unmatched_reference, unmatched_hypothesis))
    return LineErrorMetrics(
        missing_lines=len(unmatched_reference),
        extra_lines=len(unmatched_hypothesis),
        changed_lines=changed,
    )


def line_error_counts(reference: Sequence[str], hypothesis: Sequence[str]) -> tuple[int, int]:
    metrics = line_error_metrics(reference, hypothesis)
    return metrics.missing_lines, metrics.extra_lines
