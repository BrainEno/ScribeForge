from __future__ import annotations

from dataclasses import dataclass

from scribeforge.domain.content import VerifiedBook, VerifiedChapter, VerifiedPage
from scribeforge.storage.sqlite import SQLiteStore


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

    pages: list[VerifiedPage] = []
    unverified_pages: list[int] = []
    unresolved_pairs: list[UnresolvedPair] = []
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            unverified_pages.append(page.page_index)
            continue

        record = store.load_verification(verification_id)
        lines: list[str] = []
        for pair_index in range(len(record.alignment.pairs)):
            decision = store.latest_decision(project_id, page.page_index, pair_index)
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
    return VerifiedBook(project.title, tuple(pages), chapters=chapters)
