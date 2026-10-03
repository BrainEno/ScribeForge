from __future__ import annotations

import unicodedata
from collections.abc import Sequence


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


def _error_rate(reference: Sequence[str], hypothesis: Sequence[str]) -> float:
    if not reference:
        return 0.0 if not hypothesis else 1.0
    return _edit_distance(reference, hypothesis) / len(reference)


def character_error_rate(reference: str, hypothesis: str) -> float:
    return _error_rate(tuple(reference), tuple(hypothesis))


def word_error_rate(reference: str, hypothesis: str) -> float:
    return _error_rate(tuple(reference.split()), tuple(hypothesis.split()))


def _is_punctuation(character: str) -> bool:
    return unicodedata.category(character).startswith("P")


def punctuation_accuracy(reference: str, hypothesis: str) -> float:
    expected = tuple(character for character in reference if _is_punctuation(character))
    actual = tuple(character for character in hypothesis if _is_punctuation(character))
    if not expected:
        return 1.0 if not actual else 0.0
    return max(0.0, 1.0 - _edit_distance(expected, actual) / len(expected))


def line_error_counts(reference: Sequence[str], hypothesis: Sequence[str]) -> tuple[int, int]:
    # LCS keeps line order meaningful and separates omissions from hallucinated lines.
    rows, cols = len(reference), len(hypothesis)
    table = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows):
        for j in range(cols):
            if reference[i] == hypothesis[j]:
                table[i + 1][j + 1] = table[i][j] + 1
            else:
                table[i + 1][j + 1] = max(table[i][j + 1], table[i + 1][j])
    matched = table[rows][cols]
    return rows - matched, cols - matched
