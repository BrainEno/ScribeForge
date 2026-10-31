"""Page furniture: running heads, footers and folios that are not content.

A running head repeats on every page — the book title, the part title, the
publisher's line — and a folio is just a number. Neither belongs in a reflowed
EPUB: a reader does not want "V." or "23" as a paragraph between two sentences, and
every duplicated line is a line the reviewer or VLM stage can waste effort on.

Getting this wrong in the other direction is worse than leaving them in: a chapter
title printed at the top margin, or the only line on a sparse page, must never be
deleted as "furniture". Detection is therefore built on evidence that is
independent of position alone:

1. the engine declared it a ``header``/``footer`` block (contract v2), or
2. it sits in the margin band **and** repeats across pages, or
3. it sits in the margin band and its page number progresses with physical page
   indexes across at least two observed pages.

A margin line that appears on only one page is *not* furniture based on repetition
or numbering alone: that is exactly the shape of a chapter heading.

Two common real-world shapes are handled in addition to the three rules above:

* **Head + folio on one line** — a running head and a page number share a margin
  line, e.g. ``"V. 23"``. The whole line is furniture when the non-numeric part
  repeats as a running head and the numeric part parses as a folio.
* **Alternating odd/even heads** — odd pages carry the book title, even pages the
  part title. Each variant is counted against its own parity class so a short
  book still removes both heads once either side reaches the repetition threshold.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from scribeforge.domain.normalize import fold_for_comparison
from scribeforge.domain.ocr import PageOCRResult
from scribeforge.domain.structure import NumberingScheme, parse_page_label

# Fraction of page height treated as the margin band. A running head set deep into
# the text block is not furniture by position, and the repetition rule still needs
# a position bound to avoid deleting repeated body refrains.
MARGIN_BAND = 0.08

# How many pages a margin line must appear on before it is called a running head.
# Two pages can share a one-off line (a part title spread); three is a pattern.
REPETITION_MIN_PAGES = 3

# A folio is removed only when at least two observed labels progress at the same
# rate as their physical page indexes. This permits sparse benchmark samples while
# refusing to treat one arbitrary margin number as a page number.
PAGE_NUMBER_MIN_SEQUENCE_PAGES = 2

# A folio embedded in a margin line is at most this many digits long.
MAX_FOLIO_DIGITS = 4

_FOLIO_SUFFIX = re.compile(r"(\d{1," + str(MAX_FOLIO_DIGITS) + r"})\s*$")
_FOLIO_PREFIX = re.compile(r"^\s*(\d{1," + str(MAX_FOLIO_DIGITS) + r")")

REASON_DECLARED_BLOCK = "engine_block"
REASON_REPEATED_IN_MARGIN = "repeated_in_margin"
REASON_PAGE_NUMBER = "page_number"


def _in_margin(result: PageOCRResult, index: int) -> bool:
    line = result.lines[index]
    top = line.box.y
    bottom = line.box.y + line.box.height
    left = line.box.x
    right = line.box.x + line.box.width
    if result.direction.is_vertical:
        # Vertical text runs down columns; the page margin is the left/right
        # edge, not the top/bottom edge.
        return left <= MARGIN_BAND or right >= 1.0 - MARGIN_BAND
    return top <= MARGIN_BAND or bottom >= 1.0 - MARGIN_BAND


def _only_line_on_page(result: PageOCRResult) -> bool:
    return len(result.lines) <= 1


def _declared_furniture(result: PageOCRResult, index: int) -> bool:
    block = result.block_of_line(index)
    return block is not None and block.block_type.is_page_furniture


def _extract_folio_from_margin_line(text: str) -> str | None:
    """Try to read a folio embedded in a margin line.

    ``"V. 23"`` yields ``"23"``; ``"23 - 译林出版社"`` yields ``"23"``.
    Returns None when no plausible folio is present.
    """

    stripped = text.strip()
    if not stripped:
        return None

    match = _FOLIO_SUFFIX.search(stripped)
    if match:
        folio = match.group(1)
        prefix = stripped[: match.start()].strip().strip(".,:;-–—")
        if prefix and len(folio) <= MAX_FOLIO_DIGITS:
            return folio

    match = _FOLIO_PREFIX.search(stripped)
    if match:
        folio = match.group(1)
        suffix = stripped[match.end() :].strip().strip(".,:;-–—")
        if suffix and len(folio) <= MAX_FOLIO_DIGITS:
            return folio

    return None


def margin_line_keys(result: PageOCRResult) -> Mapping[str, tuple[int, ...]]:
    """Folded text -> line indexes, for margin lines that could be furniture."""

    keys: dict[str, list[int]] = {}
    for index in range(len(result.lines)):
        if not _in_margin(result, index):
            continue
        key = fold_for_comparison(result.lines[index].text)
        if not key:
            continue
        keys.setdefault(key, []).append(index)
    return {key: tuple(indexes) for key, indexes in keys.items()}


def _continuous_page_number_indexes(
    pages: Mapping[int, PageOCRResult],
) -> Mapping[int, frozenset[int]]:
    """Return margin lines whose folios progress with their physical page indexes.

    Grouping by ``printed sequence - physical page index`` permits sparse sample
    pages (e.g. folios 3, 13, 23 at ``page_index`` 19, 29, 39) while rejecting
    isolated, misread, or repeated-but-nonprogressing numbers. A numbering restart
    naturally forms a new offset group.
    """

    groups: dict[tuple[NumberingScheme, int], dict[int, set[int]]] = {}
    for page_index, result in pages.items():
        for line_index, line in enumerate(result.lines):
            if not _in_margin(result, line_index):
                continue
            label = parse_page_label(line.text)
            if not label.is_numbered or label.sequence is None:
                continue
            offset = label.sequence - page_index
            page_lines = groups.setdefault((label.scheme, offset), {})
            page_lines.setdefault(page_index, set()).add(line_index)

    confirmed: dict[int, set[int]] = {}
    for page_lines in groups.values():
        if len(page_lines) < PAGE_NUMBER_MIN_SEQUENCE_PAGES:
            continue
        for page_index, line_indexes in page_lines.items():
            confirmed.setdefault(page_index, set()).update(line_indexes)
    return {page_index: frozenset(indexes) for page_index, indexes in confirmed.items()}


def detect_page_furniture(
    pages: Mapping[int, PageOCRResult],
    *,
    repetition_min_pages: int = REPETITION_MIN_PAGES,
) -> Mapping[int, Mapping[int, str]]:
    """Return ``page_index -> {line_index: reason}`` for furniture lines.

    ``pages`` must contain every page of the book: the repetition rule is only
    meaningful across the whole document, which is why this cannot be decided one
    page at a time while OCR runs.

    Repetition is counted per ``(folded_text, page_parity)`` so an alternating
    odd/even running head (book title on recto, part title on verso) is detected
    even when each variant appears on fewer than half the pages. A key repeated on
    *all* pages is caught through the ``all`` bucket even when each parity class
    alone is below the threshold (short books).
    """

    continuous_folios = _continuous_page_number_indexes(pages)

    # key -> {"all": set of pages, 0: even pages, 1: odd pages}
    counts: dict[str, dict[str | int, set[int]]] = {}
    for page_index, result in pages.items():
        parity = page_index % 2
        for key in margin_line_keys(result):
            entry = counts.setdefault(key, {})
            entry.setdefault("all", set()).add(page_index)
            entry.setdefault(parity, set()).add(page_index)

    detected: dict[int, dict[int, str]] = {}
    for page_index, result in pages.items():
        if _only_line_on_page(result):
            # A single line is content by definition: there is nothing else on the
            # page for it to be furniture *relative to*.
            continue
        parity = page_index % 2
        found: dict[int, str] = {}
        for index in range(len(result.lines)):
            line = result.lines[index]
            if _declared_furniture(result, index):
                found[index] = REASON_DECLARED_BLOCK
                continue
            if not _in_margin(result, index):
                continue
            page_label = parse_page_label(line.text)
            if page_label.is_numbered:
                if index in continuous_folios.get(page_index, frozenset()):
                    found[index] = REASON_PAGE_NUMBER
                    continue
                if page_label.scheme not in (
                    NumberingScheme.ROMAN_UPPER,
                    NumberingScheme.ROMAN_LOWER,
                ):
                    # An isolated or non-progressing Arabic/CJK number is ordinary
                    # page text, even if the same OCR reading repeats at the edge.
                    continue
                # A repeated Roman title such as "V." is allowed to reach the
                # running-head rule below. Without an increasing sequence it is
                # not evidence of a printed folio.

            # Head + folio on one line, e.g. "V. 23": the bare folio part is
            # furniture, and the head part must repeat as a running head for the
            # whole line to be excluded.
            folio = _extract_folio_from_margin_line(line.text)
            if folio is not None:
                head = fold_for_comparison(
                    _FOLIO_SUFFIX.sub("", _FOLIO_PREFIX.sub("", line.text)).strip(
                        ".,:;-–—"
                    )
                )
                head_entry = counts.get(head, {}) if head else {}
                head_pages = head_entry.get("all", set()) or head_entry.get(
                    parity, set()
                )
                if head and len(head_pages) >= repetition_min_pages:
                    found[index] = REASON_PAGE_NUMBER
                    continue

            key = fold_for_comparison(line.text)
            entry = counts.get(key, {})
            pages_for_key = entry.get("all", set()) or entry.get(parity, set())
            if key and len(pages_for_key) >= repetition_min_pages:
                found[index] = REASON_REPEATED_IN_MARGIN
        if found:
            detected[page_index] = found
    return detected


def furniture_line_indexes(
    pages: Mapping[int, PageOCRResult],
    *,
    repetition_min_pages: int = REPETITION_MIN_PAGES,
) -> Mapping[int, frozenset[int]]:
    """Convenience wrapper returning just the line indexes to exclude."""

    detected = detect_page_furniture(pages, repetition_min_pages=repetition_min_pages)
    return {
        page_index: frozenset(found) for page_index, found in detected.items()
    }


def repeated_margin_texts(
    pages: Iterable[PageOCRResult],
    *,
    repetition_min_pages: int = REPETITION_MIN_PAGES,
) -> Mapping[str, int]:
    """Margin texts seen on enough pages to be running heads, with page counts.

    Exposed because the export report should be able to say *what* was removed, not
    only how many lines: silently dropping text is exactly the failure mode this
    module exists to avoid.
    """

    counts: dict[str, set[int]] = {}
    for result in pages:
        for key in margin_line_keys(result):
            counts.setdefault(key, set()).add(result.page_index)
    return {
        key: len(seen)
        for key, seen in counts.items()
        if len(seen) >= repetition_min_pages
    }
