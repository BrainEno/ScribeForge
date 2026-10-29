from __future__ import annotations

from dataclasses import dataclass

from scribeforge.domain.alignment import LineAlignment
from scribeforge.domain.content import (
    DroppedFurniture,
    VerifiedBook,
    VerifiedChapter,
    VerifiedPage,
)
from scribeforge.domain.furniture import detect_page_furniture
from scribeforge.domain.ocr import PageOCRResult
from scribeforge.storage.sqlite import SQLiteStore, VerificationRecord


@dataclass(frozen=True, slots=True)
class UnresolvedPair:
    page_index: int
    pair_index: int


class UnresolvedContentError(Exception):
    """Raised when export is requested while accepted text is still incomplete."""

    def __init__(
        self,
        *,
        unverified_pages: tuple[int, ...],
        unresolved_pairs: tuple[UnresolvedPair, ...],
    ) -> None:
        self.unverified_pages = unverified_pages
        self.unresolved_pairs = unresolved_pairs
        super().__init__(
            f"{len(unverified_pages)} unverified page(s), "
            f"{len(unresolved_pairs)} unresolved pair(s)"
        )


def assemble_verified_content(store: SQLiteStore, project_id: int) -> VerifiedBook:
    """Assemble the accepted text of a project from its newest decisions.

    Refuses to produce a partial book: a page without a verification run, or a
    pair without an accepted decision, blocks assembly so export can never
    silently drop or substitute unverified text.
    """

    project = next((item for item in store.list_projects() if item.id == project_id), None)
    if project is None:
        raise KeyError(f"unknown project: {project_id}")

    structure = store.load_book_structure(project_id)
    labels = structure.printed_labels_by_page()
    furniture = _detect_furniture(store, project_id)

    pages: list[VerifiedPage] = []
    dropped: list[DroppedFurniture] = []
    unverified_pages: list[int] = []
    unresolved_pairs: list[UnresolvedPair] = []
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            unverified_pages.append(page.page_index)
            continue

        record = store.load_verification(verification_id)
        page_furniture = furniture.get(page.page_index, {})
        lines: list[str] = []
        for pair_index in range(len(record.alignment.pairs)):
            pair = record.alignment.pairs[pair_index]
            furniture_reason = _pair_furniture(page_furniture, pair)
            decision = store.latest_decision(project_id, page.page_index, pair_index)
            if furniture_reason is not None:
                # A running head is not content: it must not be exported, and its
                # missing decision must not block the book from assembling.
                text = decision.selected_text if decision is not None else ""
                if not text:
                    text = _pair_source_text(store, record, pair)
                dropped.append(
                    DroppedFurniture(page.page_index, text, furniture_reason)
                )
                continue
            if decision is None:
                unresolved_pairs.append(UnresolvedPair(page.page_index, pair_index))
                continue
            lines.append(decision.selected_text)
        pages.append(
            VerifiedPage(
                page.page_index,
                tuple(lines),
                printed_label=labels.get(page.page_index, ""),
            )
        )

    if unverified_pages or unresolved_pairs:
        raise UnresolvedContentError(
            unverified_pages=tuple(unverified_pages),
            unresolved_pairs=tuple(unresolved_pairs),
        )
    exportable = {page.page_index for page in pages}
    chapters = tuple(
        VerifiedChapter(
            title=chapter.title,
            level=chapter.level,
            page_index=chapter.start_page_index,
            printed_page=chapter.printed_page,
        )
        for chapter in structure.chapters
        # A chapter anchored to a page that did not survive verification would
        # point a reader at a missing file.
        if chapter.start_page_index in exportable
    )
    return VerifiedBook(
        project.title,
        tuple(pages),
        chapters=chapters,
        dropped_furniture=tuple(dropped),
    )


def _detect_furniture(
    store: SQLiteStore, project_id: int
) -> dict[int, dict[int, str]]:
    """Margin/furniture detection over every stored run of the project.

    Uses whichever engine produced a run for the page; furniture is a property of
    the page, not of one engine, and a page missing from one engine must not lose
    its detection. Falls back to an empty result rather than failing assembly: an
    export with a stray running head is recoverable, an export that cannot run is
    not.
    """

    pages: dict[int, PageOCRResult] = {}
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            verification_id = None
        if verification_id is not None:
            result = store.load_ocr_run(
                store.load_verification(verification_id).primary_run_id
            )
        else:
            stored = _first_stored_run(store, project_id, page.page_index)
            if stored is None:
                continue
            result = stored
        pages[page.page_index] = result
    return {page_index: dict(found) for page_index, found in detect_page_furniture(pages).items()}


def _first_stored_run(
    store: SQLiteStore, project_id: int, page_index: int
) -> PageOCRResult | None:
    for engine in ("primary", "secondary"):
        try:
            run_id = store.latest_engine_run(project_id, page_index, engine)
        except KeyError:
            continue
        return store.load_ocr_run(run_id)
    return None


def _pair_furniture(
    page_furniture: dict[int, str], pair: LineAlignment
) -> str | None:
    """Reason string when either side of the pair is furniture."""

    for index in (pair.primary_line_index, pair.secondary_line_index):
        if index is not None and index in page_furniture:
            return page_furniture[index]
    return None


def _pair_source_text(
    store: SQLiteStore, record: VerificationRecord, pair: LineAlignment
) -> str:
    primary = store.load_ocr_run(record.primary_run_id)
    if pair.primary_line_index is not None:
        return primary.lines[pair.primary_line_index].text
    secondary = store.load_ocr_run(record.secondary_run_id)
    if pair.secondary_line_index is not None:
        return secondary.lines[pair.secondary_line_index].text
    return ""
