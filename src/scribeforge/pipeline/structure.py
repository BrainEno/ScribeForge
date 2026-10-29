"""Derive book structure from OCR evidence.

Nothing here invents text: every role, page number and chapter title comes from a
line or block an engine actually reported, filtered through conservative rules.
Where the evidence is ambiguous the page stays ``UNKNOWN`` and gets no printed
label, because a wrong folio silently corrupts every cross-reference built on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from scribeforge.domain.ocr import BlockType, OCRLine, PageOCRResult
from scribeforge.domain.structure import (
    BookStructure,
    Chapter,
    NumberingScheme,
    PageLabel,
    PageRole,
    PageStructure,
    assign_sequences,
    looks_like_heading,
    parse_page_label,
)
from scribeforge.storage.sqlite import SQLiteStore

# A margin folio lives in the outer band of the page. Wider than this and a body
# line can qualify; narrower and a folio pushed in by a wide gutter is missed.
_MARGIN_BAND = 0.10
# Headings are set larger than body text. Body height is estimated per page.
_HEADING_LEVEL_MIN_RATIO = 1.15


@dataclass(frozen=True, slots=True)
class StructureReport:
    pages: tuple[int, ...]
    labelled_pages: tuple[int, ...]
    chapters: int
    roles: dict[str, int]


def _margin_candidates(result: PageOCRResult) -> list[OCRLine]:
    """Lines in the top or bottom band, preferring declared header/footer blocks."""

    declared = [
        result.lines[index]
        for index in range(len(result.lines))
        if (block := result.block_of_line(index)) is not None
        and block.block_type.is_page_furniture
    ]
    if declared:
        return declared
    candidates: list[OCRLine] = []
    for line in result.lines:
        top = line.box.y
        bottom = line.box.y + line.box.height
        if top <= _MARGIN_BAND or bottom >= 1.0 - _MARGIN_BAND:
            candidates.append(line)
    return candidates


UNNUMBERED = PageLabel("", NumberingScheme.NONE, None)


def detect_page_label(result: PageOCRResult) -> PageLabel:
    """Best printed-page-number candidate on a page, or an unnumbered label.

    Only the margin band is considered. Body text contains numbers constantly
    ("1923", "第十二章"), and treating one of those as a folio would place every
    following page at the wrong label.
    """

    for line in _margin_candidates(result):
        candidate = parse_page_label(line.text)
        if candidate.is_numbered:
            return candidate
    return UNNUMBERED


def _body_height(result: PageOCRResult) -> float:
    """Median line height of *body* lines, excluding declared titles.

    Including the heading itself poisons the estimate on a two-line page: the
    median of [heading, body] is the heading, every heading then measures 1.0x and
    no chapter is ever detected.
    """

    heights = sorted(
        line.box.height
        for index, line in enumerate(result.lines)
        if line.box.height > 0
        and not (
            (block := result.block_of_line(index)) is not None
            and block.block_type is BlockType.TITLE
        )
    )
    if not heights:
        return 0.0
    return heights[len(heights) // 2]


def detect_chapters(
    result: PageOCRResult, page_index: int
) -> tuple[tuple[int, str], ...]:
    """Headings on one page, with a level inferred from their typeset size.

    Two independent signals are required, never a single guess:

    - the engine labelled the line as a title block, or the text matches a
      chapter/part pattern;
    - the line is typeset larger than this page's body text, or it is a declared
      title block (some engines report no usable geometry for headings).
    """

    body = _body_height(result)
    found: list[tuple[int, str]] = []
    for index in range(len(result.lines)):
        line = result.lines[index]
        block = result.block_of_line(index)
        declared_title = block is not None and block.block_type is BlockType.TITLE
        if not declared_title and not looks_like_heading(line.text):
            continue
        if not declared_title and body > 0 and line.box.height < body * (
            _HEADING_LEVEL_MIN_RATIO
        ):
            continue
        ratio = (line.box.height / body) if body > 0 else 1.0
        level = 1 if ratio >= 1.6 else 2 if ratio >= 1.3 else 3
        found.append((level, line.text.strip()))
    return tuple(found)


def _page_text(result: PageOCRResult) -> str:
    return "".join(result.lines[index].text for index in result.text_line_indexes)


def build_book_structure(
    store: SQLiteStore,
    project_id: int,
    *,
    engine: str,
) -> BookStructure:
    """Read the latest OCR run of each page and derive the whole-book structure.

    Page labels are resolved across the whole book in one pass, because numbering
    sequences only make sense as a sequence: a footer reading "12" means nothing
    until it is placed relative to the pages around it.
    """

    candidates: list[tuple[int, PageLabel]] = []
    per_page: dict[int, PageOCRResult] = {}
    for page in store.list_pages(project_id):
        try:
            run_id = store.latest_engine_run(project_id, page.page_index, engine)
        except KeyError:
            continue
        result = store.load_ocr_run(run_id)
        per_page[page.page_index] = result
        label = detect_page_label(result)
        if label.is_numbered:
            candidates.append((page.page_index, label))

    resolved = assign_sequences(candidates)

    structures: list[PageStructure] = []
    chapters: list[Chapter] = []
    ordinal = 0
    for page_index in sorted(per_page):
        result = per_page[page_index]
        provided = resolved.get(page_index, UNNUMBERED)
        text = _page_text(result)
        role = PageRole.BODY if text.strip() else PageRole.BLANK
        structures.append(
            PageStructure(page_index=page_index, role=role, label=provided)
        )
        # Never take a chapter title from an untextual page: the cover's title is
        # not a chapter.
        for level, title in detect_chapters(result, page_index):
            chapters.append(
                Chapter(
                    ordinal=ordinal,
                    level=level,
                    title=title,
                    start_page_index=page_index,
                    printed_page=provided.printed_label,
                )
            )
            ordinal += 1

    return BookStructure(tuple(structures), tuple(chapters))


def persist_book_structure(
    store: SQLiteStore,
    project_id: int,
    structure: BookStructure,
) -> StructureReport:
    """Store a derived structure and describe what was found."""

    store.record_page_structure(project_id, structure.pages)
    store.record_chapters(project_id, structure.chapters)
    roles: dict[str, int] = {}
    for page in structure.pages:
        roles[page.role.value] = roles.get(page.role.value, 0) + 1
    return StructureReport(
        pages=tuple(page.page_index for page in structure.pages),
        labelled_pages=tuple(
            page.page_index for page in structure.pages if page.label.is_numbered
        ),
        chapters=len(structure.chapters),
        roles=roles,
    )
