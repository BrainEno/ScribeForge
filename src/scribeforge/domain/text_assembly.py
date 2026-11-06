"""Assemble source OCR lines without rewriting the source evidence.

OCR line text is immutable.  Alignment, adjudication and export nevertheless need
one string for a 1:N or N:1 group.  This module is the single place that derives
that string.  Existing whitespace is preserved verbatim; a separator is inferred
only at a small set of explicit script/punctuation boundaries.

This is not a reflow or paragraph-recovery algorithm.  In particular it does not
remove hyphenation, trim lines, translate text, or normalize glyph variants.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

__all__ = ["assemble_group_text"]

# A closing mark directly after a source line does not gain a leading space.
# Opening marks and hyphens at the end of the preceding line likewise bind to the
# next source line.  Apostrophes are included so ``don`` + ``'t`` stays ``don't``.
_NO_SPACE_BEFORE = frozenset("([{<)]}>,.;:!?%'’”»…")
_NO_SPACE_AFTER = frozenset("([{<'‘“«-‐‑‒–")


def _is_latin_word_character(character: str) -> bool:
    if character.isascii():
        return character.isalnum()
    return "LATIN" in unicodedata.name(character, "") and (
        character.isalpha() or character.isdecimal()
    )


def _needs_latin_space(left: str, right: str) -> bool:
    if left.isspace() or right.isspace():
        return False
    if right in _NO_SPACE_BEFORE or left in _NO_SPACE_AFTER:
        return False
    if not (_is_latin_word_character(left) and _is_latin_word_character(right)):
        return False
    # Purely numeric runs are commonly split inside one number.  With no Latin
    # letter on either side there is not enough evidence to infer a word space.
    return left.isalpha() or right.isalpha()


def assemble_group_text(texts: Iterable[str]) -> str:
    """Return the consistent comparison/decision/export text for an OCR group.

    CJK boundaries receive no invented separator.  A boundary between two Latin
    word characters receives one ASCII space unless existing whitespace or an
    explicit punctuation/hyphen rule already joins them.  No input string is
    stripped or otherwise modified.
    """

    assembled = ""
    for text in texts:
        if not isinstance(text, str):
            raise TypeError("OCR group text must be a string")
        if assembled and text and _needs_latin_space(assembled[-1], text[0]):
            assembled += " "
        assembled += text
    return assembled
