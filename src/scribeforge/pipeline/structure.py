"""Derive book structure from OCR evidence.

Nothing here invents text: every role, page number and chapter title comes from a
line or block an engine actually reported, filtered through conservative rules.
Where the evidence is ambiguous the page stays ``UNKNOWN`` and gets no printed
label, because a wrong folio silently corrupts every cross-reference built on it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scribeforge.adapters.page_ink import InkMeasurement, measure_page_ink
from scribeforge.domain.ocr import BlockType, OCRLine, PageOCRResult
from scribeforge.domain.structure import (
    BLANK_INK_RATIO,
    BookStructure,
    Chapter,
    NumberingScheme,
    PageLabel,
    PageRole,
    PageStructure,
    RoleEvidence,
    TocEntry,
    TocMapping,
    TocPage,
    assign_sequences,
    classify_page_roles,
    looks_like_heading,
    map_toc_chapters,
    parse_page_label,
    parse_toc_entries,
)
from scribeforge.storage.sqlite import SQLiteStore

# A margin folio lives in the outer band of the page. Wider than this and a body
# line can qualify; narrower and a folio pushed in by a wide gutter is missed.
_MARGIN_BAND = 0.10
# Headings are set larger than body text. Body height is estimated per page.
_HEADING_LEVEL_MIN_RATIO = 1.15


@dataclass(frozen=True, slots=True)
class StructureReport:
    """What one structure pass found, in numbers an operator can check."""

    pages: tuple[int, ...]
    labelled_pages: tuple[int, ...]
    chapters: int
    roles: dict[str, int]
    #: Where the chapter tree came from: ``toc`` (the book's own contents page) or
    #: ``heading`` (per-page typography), or ``none`` when nothing was found.
    chapter_source: str = "none"
    #: TOC entries read, and how many could be anchored to a physical page.
    toc_entries: int = 0
    toc_mapped: int = 0
    #: TOC entries that no page could be proved to match. Reported, not guessed.
    toc_unmapped: tuple[str, ...] = ()
    #: Pages measured as blank or as plates, and pages with no ink measurement.
    blank_pages: tuple[int, ...] = ()
    plate_pages: tuple[int, ...] = ()
    unmeasured_pages: tuple[int, ...] = ()
    #: Pages confirmed textless from their measured ink during this pass.
    confirmed_textless: tuple[int, ...] = ()


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


def measure_ink(
    store: SQLiteStore, project_id: int, page_indexes: list[int] | None = None
) -> dict[int, InkMeasurement]:
    """Measure ink coverage of the stored page rasters.

    A missing or unreadable raster is *not* an error: a restored project may have
    moved its assets, and "no measurement" must stay distinguishable from
    "measured zero ink" — one blocks a claim, the other supports it.
    """

    wanted = set(page_indexes) if page_indexes is not None else None
    measured: dict[int, InkMeasurement] = {}
    for page in store.list_pages(project_id):
        if wanted is not None and page.page_index not in wanted:
            continue
        try:
            measured[page.page_index] = measure_page_ink(Path(page.source_image))
        except (OSError, ValueError):
            continue
    return measured


@dataclass(frozen=True, slots=True)
class _PageEvidence:
    """One page's raw material: what the engine read and what the raster measures."""

    page_index: int
    lines: tuple[str, ...]
    label: PageLabel
    ink_ratio: float | None
    headings: tuple[tuple[int, str], ...]

    @property
    def heading_titles(self) -> tuple[str, ...]:
        return tuple(title for _, title in self.headings)


def build_book_structure(
    store: SQLiteStore,
    project_id: int,
    *,
    engine: str,
    measure_ink_ratio: bool = True,
) -> BookStructure:
    """Read the latest OCR run of each page and derive the whole-book structure.

    Page labels are resolved across the whole book in one pass, because numbering
    sequences only make sense as a sequence: a footer reading "12" means nothing
    until it is placed relative to the pages around it. Roles and the chapter tree
    then follow from that sequence plus the measured ink on each page.

    ``measure_ink_ratio=False`` exists for callers that only want labels and
    chapters (and for stores whose rasters have been moved): roles then fall back
    to "text or no text" and no page is confirmed textless from measurement.
    """

    candidates: list[tuple[int, PageLabel]] = []
    results: dict[int, PageOCRResult] = {}
    for page in store.list_pages(project_id):
        try:
            run_id = store.latest_engine_run(project_id, page.page_index, engine)
        except KeyError:
            continue
        result = store.load_ocr_run(run_id)
        results[page.page_index] = result
        label = detect_page_label(result)
        if label.is_numbered:
            candidates.append((page.page_index, label))

    resolved = assign_sequences(candidates)
    ink = (
        measure_ink(store, project_id, sorted(results)) if measure_ink_ratio else {}
    )

    evidence = tuple(
        _PageEvidence(
            page_index=page_index,
            lines=tuple(result.lines[index].text for index in result.text_line_indexes),
            label=resolved.get(page_index, UNNUMBERED),
            ink_ratio=None if page_index not in ink else ink[page_index].ratio,
            headings=detect_chapters(result, page_index),
        )
        for page_index, result in sorted(results.items())
    )
    roles = classify_page_roles(
        tuple(
            RoleEvidence(
                page_index=item.page_index,
                lines=item.lines,
                label=item.label,
                ink_ratio=item.ink_ratio,
                headings=item.heading_titles,
            )
            for item in evidence
        )
    )

    structures = tuple(
        PageStructure(
            page_index=item.page_index,
            role=roles.get(item.page_index, PageRole.UNKNOWN),
            label=item.label,
            ink_ratio=item.ink_ratio,
        )
        for item in evidence
    )

    toc_entries, mapping = _read_toc(evidence, roles)
    heading_chapters = _chapters_from_headings(evidence, roles)
    # A contents page is the author's own list of chapters, so it wins whenever its
    # entries can be proved against the pages themselves; per-page typography is
    # the fallback, not the other way round.
    if len(mapping.chapters) >= 2:
        chapters = mapping.chapters
    else:
        chapters = heading_chapters
    return BookStructure(
        structures,
        chapters,
        toc_entries=len(toc_entries),
        unplaced_toc_titles=tuple(entry.title for entry in mapping.unmapped),
    )


def _read_toc(
    evidence: tuple[_PageEvidence, ...], roles: Mapping[int, PageRole]
) -> tuple[tuple[TocEntry, ...], TocMapping]:
    """Collect every contents page of the book and anchor its entries."""

    entries: list[TocEntry] = []
    for item in evidence:
        if roles.get(item.page_index) is PageRole.TABLE_OF_CONTENTS:
            entries.extend(parse_toc_entries(item.lines))
    pages = tuple(
        TocPage(page_index=item.page_index, label=item.label, headings=item.heading_titles)
        for item in evidence
    )
    return tuple(entries), map_toc_chapters(tuple(entries), pages)


def _chapters_from_headings(
    evidence: tuple[_PageEvidence, ...], roles: Mapping[int, PageRole]
) -> tuple[Chapter, ...]:
    """Chapter tree from headings found on the pages themselves."""

    chapters: list[Chapter] = []
    for item in evidence:
        if not roles.get(item.page_index, PageRole.UNKNOWN).is_textual:
            # Never take a chapter title from a non-textual page: the cover's own
            # title is not a chapter of the book.
            continue
        for level, title in item.headings:
            chapters.append(
                Chapter(
                    ordinal=len(chapters),
                    level=level,
                    title=title,
                    start_page_index=item.page_index,
                    printed_page=item.label.printed_label,
                    source="heading",
                )
            )
    return tuple(chapters)


def persist_book_structure(
    store: SQLiteStore,
    project_id: int,
    structure: BookStructure,
    *,
    confirm_textless: bool = True,
) -> StructureReport:
    """Store a derived structure and describe what was found.

    Pages measured as carrying no ink at all are also *confirmed* textless here, as
    ``evidence`` rather than as a person's claim — but only below
    ``BLANK_INK_RATIO``, where the page has essentially nothing on it. That narrow
    band is the one fact a measurement can establish on its own, and recording it
    lets a genuinely blank leaf leave the queue without anyone clicking through it.
    Everything above that band is a decision, not a measurement: a plate, a page the
    engines failed to read, and a nearly empty page with one line of type all keep
    their ink and stay blocked until a person says what they are.
    """

    store.record_page_structure(project_id, structure.pages)
    store.record_chapters(project_id, structure.chapters)
    roles: dict[str, int] = {}
    for page in structure.pages:
        roles[page.role.value] = roles.get(page.role.value, 0) + 1
    blank = tuple(page.page_index for page in structure.pages if page.role is PageRole.BLANK)
    plate = tuple(page.page_index for page in structure.pages if page.role is PageRole.PLATE)
    unmeasured = tuple(
        page.page_index
        for page in structure.pages
        if page.role is PageRole.BLANK and page.ink_ratio is None
    )
    confirmed: list[int] = []
    if confirm_textless:
        for page in structure.pages:
            # Only pages with essentially no ink at all. A page between the blank
            # threshold and the plate threshold is textless *by report*, which is
            # exactly the ambiguous case a person has to settle.
            if (
                page.role is not PageRole.BLANK
                or page.ink_ratio is None
                or page.ink_ratio >= BLANK_INK_RATIO
            ):
                continue
            store.record_page_confirmation(
                project_id=project_id,
                page_index=page.page_index,
                kind="blank",
                actor="evidence",
                reason=(
                    "measured ink coverage "
                    f"{page.ink_ratio:.2%} is below the {BLANK_INK_RATIO:.0%} "
                    "blank-page threshold"
                ),
                ink_ratio=page.ink_ratio,
            )
            confirmed.append(page.page_index)
    sources = {chapter.source for chapter in structure.chapters}
    chapter_source = (
        "toc"
        if "toc" in sources
        else "heading"
        if "heading" in sources
        else "manual"
        if "manual" in sources
        else "none"
    )
    return StructureReport(
        pages=tuple(page.page_index for page in structure.pages),
        labelled_pages=tuple(
            page.page_index for page in structure.pages if page.label.is_numbered
        ),
        chapters=len(structure.chapters),
        roles=roles,
        chapter_source=chapter_source,
        toc_entries=structure.toc_entries,
        toc_mapped=sum(
            1 for chapter in structure.chapters if chapter.source == "toc"
        ),
        toc_unmapped=structure.unplaced_toc_titles,
        blank_pages=blank,
        plate_pages=plate,
        unmeasured_pages=unmeasured,
        confirmed_textless=tuple(confirmed),
    )
