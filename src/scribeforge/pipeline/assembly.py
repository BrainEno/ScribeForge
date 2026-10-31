from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from scribeforge.domain.acceptance import (
    DEFAULT_POLICY,
    AcceptancePolicy,
    AcceptanceSummary,
    SuspectAcceptance,
    has_unrepresentable_characters,
    machine_blockers,
    unrepresentable_characters,
)
from scribeforge.domain.alignment import LineAlignment
from scribeforge.domain.content import (
    IMAGE_MEDIA_TYPES,
    DroppedFurniture,
    PageImageRole,
    VerifiedBook,
    VerifiedChapter,
    VerifiedImage,
    VerifiedPage,
)
from scribeforge.domain.furniture import detect_page_furniture
from scribeforge.domain.ocr import BlockType, BoundingBox, PageOCRResult
from scribeforge.domain.paragraphs import LineRole
from scribeforge.domain.structure import BookStructure, PageRole
from scribeforge.storage.sqlite import PageConfirmation, SQLiteStore, VerificationRecord


@dataclass(frozen=True, slots=True)
class UnresolvedPair:
    """One aligned pair that still needs a person.

    ``blockers`` says why the pipeline would not accept it, and the offending
    ``characters`` when the reason is unreadable text. Both are excluded from
    comparison: two pairs are the same pair whether or not the diagnosis is at hand.
    """

    page_index: int
    pair_index: int
    blockers: tuple[str, ...] = field(default=(), compare=False)
    characters: tuple[str, ...] = field(default=(), compare=False)


class UnresolvedContentError(Exception):
    """Raised when export is requested while accepted text is still incomplete.

    ``unconfirmed_pages`` is the third way a book can be incomplete: a page where
    the engines reported no lines at all. That is ambiguous — blank leaf,
    illustration, crashed worker, or a page nobody managed to read — so it blocks
    export until someone confirms it (issue #74).
    """

    def __init__(
        self,
        *,
        unverified_pages: tuple[int, ...],
        unresolved_pairs: tuple[UnresolvedPair, ...],
        unconfirmed_pages: tuple[int, ...] = (),
        measured_ink: Mapping[int, float] | None = None,
    ) -> None:
        self.unverified_pages = unverified_pages
        self.unresolved_pairs = unresolved_pairs
        self.unconfirmed_pages = unconfirmed_pages
        #: Ink coverage of each unconfirmed page, when it could be measured. An
        #: operator deciding "is this really blank?" is helped by knowing that the
        #: page measures 0.4% ink rather than 12%: the first is a leaf, the second
        #: is a page the engines failed to read.
        self.measured_ink = dict(measured_ink or {})
        super().__init__(
            f"{len(unverified_pages)} unverified page(s), "
            f"{len(unresolved_pairs)} unresolved pair(s), "
            f"{len(unconfirmed_pages)} unconfirmed textless page(s)"
        )


def assemble_verified_content(
    store: SQLiteStore,
    project_id: int,
    *,
    policy: AcceptancePolicy = DEFAULT_POLICY,
    include_page_furniture: bool = False,
) -> VerifiedBook:
    """Assemble the accepted text of a project from its newest decisions.

    Refuses to produce a partial book: a page without a verification run, or a
    pair without an accepted decision, blocks assembly so export can never
    silently drop or substitute unverified text. Detected page furniture is
    excluded by default; ``include_page_furniture`` preserves it, but does not
    bypass the normal decision gate for unresolved pairs.
    """

    project = next((item for item in store.list_projects() if item.id == project_id), None)
    if project is None:
        raise KeyError(f"unknown project: {project_id}")

    structure = store.load_book_structure(project_id)
    labels = structure.printed_labels_by_page()
    furniture = (
        {} if include_page_furniture else _detect_furniture(store, project_id)
    )
    confirmations = store.latest_page_confirmations(project_id)
    measured_ink = {
        entry.page_index: entry.ink_ratio
        for entry in structure.pages
        if entry.ink_ratio is not None
    }

    pages: list[VerifiedPage] = []
    dropped: list[DroppedFurniture] = []
    unverified_pages: list[int] = []
    unresolved_pairs: list[UnresolvedPair] = []
    unconfirmed_pages: list[int] = []
    confirmed_blank: list[int] = []
    empty_pairs: list[tuple[int, int]] = []
    machine_accepted = 0
    human_accepted = 0
    max_machine_content_risk = 0.0
    spot_check: list[SuspectAcceptance] = []
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            unverified_pages.append(page.page_index)
            continue

        record = store.load_verification(verification_id)
        if not record.alignment.pairs:
            # Zero pairs is not evidence of a blank page: two crashed workers, a
            # mis-decoded raster and an empty leaf all look like this. Only an
            # explicit confirmation makes it exportable.
            if page.page_index in confirmations:
                confirmed_blank.append(page.page_index)
                pages.append(
                    VerifiedPage(
                        page.page_index,
                        (),
                        printed_label=labels.get(page.page_index, ""),
                        direction=record.alignment.direction,
                    )
                )
            else:
                unconfirmed_pages.append(page.page_index)
            continue
        page_furniture = furniture.get(page.page_index, {})
        primary_result = store.load_ocr_run(record.primary_run_id)
        secondary_result = store.load_ocr_run(record.secondary_run_id)
        lines: list[str] = []
        boxes: list[BoundingBox | None] = []
        roles: list[LineRole] = []
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
                if _pair_is_empty(primary_result, secondary_result, pair):
                    # Both engines reported an empty line: there is no text to
                    # accept and nothing for a person to decide.
                    empty_pairs.append((page.page_index, pair_index))
                    continue
                risk = record.risks[pair_index] if pair_index < len(record.risks) else None
                # Diagnose the refusal here, where both readings are in hand, so the
                # export error can say *why* each pair is still open.
                primary_text = _pair_text(primary_result, pair.primary_line_index)
                secondary_text = _pair_text(secondary_result, pair.secondary_line_index)
                unresolved_pairs.append(
                    UnresolvedPair(
                        page.page_index,
                        pair_index,
                        blockers=machine_blockers(
                            text=primary_text or secondary_text or "",
                            risk=risk,
                            policy=policy,
                        ),
                        characters=unrepresentable_characters(
                            (primary_text or "") + (secondary_text or "")
                        ),
                    )
                )
                continue
            lines.append(decision.selected_text)
            boxes.append(_pair_box(primary_result, secondary_result, pair))
            roles.append(_pair_role(primary_result, secondary_result, pair))
            risk = record.risks[pair_index] if pair_index < len(record.risks) else None
            content_risk = 0.0 if risk is None else risk.content_score
            if decision.actor == "human":
                human_accepted += 1
            else:
                machine_accepted += 1
                max_machine_content_risk = max(max_machine_content_risk, content_risk)
            note = ""
            if has_unrepresentable_characters(decision.selected_text):
                note = "accepted text contains a character no export can carry"
            if note or content_risk >= policy.spot_check_risk:
                spot_check.append(
                    SuspectAcceptance(
                        page_index=page.page_index,
                        pair_index=pair_index,
                        actor=decision.actor,
                        score=content_risk,
                        reasons=() if risk is None else risk.reasons,
                        note=note,
                    )
                )
        pages.append(
            VerifiedPage(
                page.page_index,
                tuple(lines),
                printed_label=labels.get(page.page_index, ""),
                direction=record.alignment.direction,
                line_boxes=tuple(boxes),
                line_roles=tuple(roles),
            )
        )

    if unverified_pages or unresolved_pairs or unconfirmed_pages:
        raise UnresolvedContentError(
            unverified_pages=tuple(unverified_pages),
            unresolved_pairs=tuple(unresolved_pairs),
            unconfirmed_pages=tuple(unconfirmed_pages),
            measured_ink=measured_ink,
        )
    exportable = {page.page_index for page in pages}
    images = _page_images(store, project_id, pages, structure, confirmations)
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
        confirmed_blank_pages=tuple(confirmed_blank),
        empty_pairs=tuple(empty_pairs),
        images=images,
        acceptance=AcceptanceSummary(
            pages=len(pages),
            pairs=machine_accepted + human_accepted,
            machine_accepted=machine_accepted,
            human_accepted=human_accepted,
            max_machine_risk=max_machine_content_risk,
            spot_check=tuple(spot_check),
        ),
    )


#: Confirmation kind recorded when a page is a picture rather than a leaf.
_NON_TEXT_KIND = "non_text"


class PageImageError(ValueError):
    """Raised when a page that must be carried as an image cannot be.

    Silently omitting it is the failure this class exists to prevent: a book
    exported without its cover, or without a plate someone confirmed, looks
    complete and is not.
    """


def _page_images(
    store: SQLiteStore,
    project_id: int,
    pages: list[VerifiedPage],
    structure: BookStructure,
    confirmations: Mapping[int, PageConfirmation],
) -> tuple[VerifiedImage, ...]:
    """The pages that must ship as pictures, with their bytes and digests.

    Two facts have to line up before a raster is embedded, and both come from
    recorded evidence rather than inference:

    1. the page has **no accepted text** — a page of prose is re-flowed, never
       mirrored, because a raster of text is exactly what this project promises
       not to ship;
    2. the page *is* a picture: the structure pass measured it as a cover or a
       plate (`page_structure`, issue #87), or a person confirmed its content is
       not text (`scribeforge confirm --kind non_text`, issue #74).

    A confirmed *blank* leaf satisfies (1) but not (2): there is nothing to look
    at, and a white rectangle in the reading flow is noise. A page nobody has
    spoken for — no role and no confirmation — is not embedded either; that page
    blocks export anyway unless both engines read it as empty.
    """

    roles = {entry.page_index: entry.role for entry in structure.pages}
    assets = {page.page_index: page.source_image for page in store.list_pages(project_id)}
    images: list[VerifiedImage] = []
    for page in pages:
        confirmation = confirmations.get(page.page_index)
        role = roles.get(page.page_index, PageRole.UNKNOWN)
        # Only a plate may carry a line of text (its caption); every other page
        # that ships as an image must be textless. A page of prose is re-flowed,
        # never mirrored.
        if page.lines and role is not PageRole.PLATE:
            continue
        if role is PageRole.COVER:
            image_role = PageImageRole.COVER
            caption = ""
        elif role is PageRole.PLATE:
            image_role = PageImageRole.FIGURE
            # A plate may carry exactly one line of OCR text beneath the image:
            # the picture's caption. More than one line means the page is not a
            # pure plate and must stay a reflowed document.
            caption = page.lines[0] if len(page.lines) == 1 else ""
            if len(page.lines) > 1:
                continue
        elif confirmation is not None and confirmation.kind == _NON_TEXT_KIND:
            # A person-confirmed non-text page must carry no text at all: a
            # single OCR line under a plate is handled through the PLATE role
            # above, not through a manual confirmation.
            if page.lines:
                continue
            image_role = PageImageRole.FIGURE
            caption = ""
        else:
            continue
        images.append(
            _load_image(page, assets[page.page_index], image_role, caption=caption)
        )
    return tuple(images)


def _load_image(
    page: VerifiedPage, source: str, role: PageImageRole, caption: str = ""
) -> VerifiedImage:
    """Read one page raster and record its digest, or refuse with the reason."""

    path = Path(source)
    suffix = path.suffix.lower()
    media_type = IMAGE_MEDIA_TYPES.get(suffix)
    if media_type is None:
        raise PageImageError(
            f"page {page.page_index} must be embedded as an image, but its raster "
            f"{path.name!r} is not a format an EPUB reader can show "
            f"({', '.join(sorted(IMAGE_MEDIA_TYPES))})"
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise PageImageError(
            f"page {page.page_index} must be embedded as an image, but its raster "
            f"{path} is unreadable: {exc.strerror or exc}"
        ) from exc
    if not data:
        raise PageImageError(
            f"page {page.page_index} must be embedded as an image, but its raster "
            f"{path} is empty"
        )
    label = page.printed_label or str(page.page_index + 1)
    alt = "封面" if role is PageImageRole.COVER else f"第 {label} 页的插图"
    return VerifiedImage(
        page_index=page.page_index,
        file_name=f"page-{page.page_index + 1:06d}{suffix}",
        media_type=media_type,
        role=role,
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        alt=alt,
        caption=caption,
    )


def _pair_text(result: PageOCRResult, index: int | None) -> str | None:
    return None if index is None else result.lines[index].text


def _pair_is_empty(
    primary: PageOCRResult, secondary: PageOCRResult, pair: LineAlignment
) -> bool:
    """True when neither engine returned any text for this pair."""

    texts = [
        result.lines[index].text
        for result, index in (
            (primary, pair.primary_line_index),
            (secondary, pair.secondary_line_index),
        )
        if index is not None
    ]
    return bool(texts) and all(not text.strip() for text in texts)


def _pair_line(
    primary: PageOCRResult, secondary: PageOCRResult, pair: LineAlignment
) -> tuple[PageOCRResult, int] | None:
    """Which engine's line represents this pair, and at which index."""

    if pair.primary_line_index is not None:
        return primary, pair.primary_line_index
    if pair.secondary_line_index is not None:
        return secondary, pair.secondary_line_index
    return None


def _pair_box(
    primary: PageOCRResult, secondary: PageOCRResult, pair: LineAlignment
) -> BoundingBox | None:
    """Box of the line that stands for this pair, for layout reconstruction."""

    source = _pair_line(primary, secondary, pair)
    if source is None:
        return None
    result, index = source
    return result.lines[index].box


def _pair_role(
    primary: PageOCRResult, secondary: PageOCRResult, pair: LineAlignment
) -> LineRole:
    """Block label of this pair, mapped onto an export role.

    Both engines are consulted: a title one engine labelled and the other did not
    is still a title, and a footnote that only one engine boxed must not be
    rendered as body text.
    """

    for result, index in (
        (primary, pair.primary_line_index),
        (secondary, pair.secondary_line_index),
    ):
        if index is None:
            continue
        block = result.block_of_line(index)
        if block is None:
            continue
        if block.block_type is BlockType.TITLE:
            return LineRole.HEADING
        if block.block_type is BlockType.FOOTNOTE:
            return LineRole.FOOTNOTE
    return LineRole.BODY


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
    """Reason for furniture mapped from the primary OCR line index."""

    # Detection indexes come from the primary OCR run. A secondary line index may
    # collide with a different primary line when the engines emit different line
    # counts; comparing it here can silently drop real body text.
    index = pair.primary_line_index
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
