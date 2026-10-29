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
3. it sits in the margin band and is a bare page number.

A margin line that appears on only one page is *not* furniture: that is exactly
the shape of a chapter heading.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from scribeforge.domain.normalize import fold_for_comparison
from scribeforge.domain.ocr import PageOCRResult
from scribeforge.domain.structure import parse_page_label

# Fraction of page height treated as the margin band. A running head set deep into
# the text block is not furniture by position, and the repetition rule still needs
# a position bound to avoid deleting repeated body refrains.
MARGIN_BAND = 0.08

# How many pages a margin line must appear on before it is called a running head.
# Two pages can share a one-off line (a part title spread); three is a pattern.
REPETITION_MIN_PAGES = 3


REASON_DECLARED_BLOCK = "engine_block"
REASON_REPEATED_IN_MARGIN = "repeated_in_margin"
REASON_PAGE_NUMBER = "page_number"


def _in_margin(result: PageOCRResult, index: int) -> bool:
    line = result.lines[index]
    top = line.box.y
    bottom = line.box.y + line.box.height
    return top <= MARGIN_BAND or bottom >= 1.0 - MARGIN_BAND


def _only_line_on_page(result: PageOCRResult) -> bool:
    return len(result.lines) <= 1


def _declared_furniture(result: PageOCRResult, index: int) -> bool:
    block = result.block_of_line(index)
    return block is not None and block.block_type.is_page_furniture


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


def detect_page_furniture(
    pages: Mapping[int, PageOCRResult],
    *,
    repetition_min_pages: int = REPETITION_MIN_PAGES,
) -> Mapping[int, Mapping[int, str]]:
    """Return ``page_index -> {line_index: reason}`` for furniture lines.

    ``pages`` must contain every page of the book: the repetition rule is only
    meaningful across the whole document, which is why this cannot be decided one
    page at a time while OCR runs.
    """

    counts: dict[str, set[int]] = {}
    for page_index, result in pages.items():
        for key in margin_line_keys(result):
            counts.setdefault(key, set()).add(page_index)

    detected: dict[int, dict[int, str]] = {}
    for page_index, result in pages.items():
        if _only_line_on_page(result):
            # A single line is content by definition: there is nothing else on the
            # page for it to be furniture *relative to*.
            continue
        found: dict[int, str] = {}
        for index in range(len(result.lines)):
            line = result.lines[index]
            if _declared_furniture(result, index):
                found[index] = REASON_DECLARED_BLOCK
                continue
            if not _in_margin(result, index):
                continue
            if parse_page_label(line.text).is_numbered:
                found[index] = REASON_PAGE_NUMBER
                continue
            key = fold_for_comparison(line.text)
            if key and len(counts.get(key, ())) >= repetition_min_pages:
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
