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
    VerifiedInlineFigure,
    VerifiedPage,
)
from scribeforge.domain.ocr import BlockType, BoundingBox, PageOCRResult
from scribeforge.domain.page_asset import PageAssetError, verify_page_asset
from scribeforge.domain.paragraphs import LineRole
from scribeforge.domain.structure import BookStructure, PageRole
from scribeforge.domain.text_assembly import assemble_group_text
from scribeforge.pipeline.furniture import (
    decision_preserves_furniture,
    pair_furniture_reason,
    project_furniture,
)
from scribeforge.pipeline.import_integrity import require_completed_import
from scribeforge.storage.sqlite import PageConfirmation, PageRecord, SQLiteStore, VerificationRecord


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

    require_completed_import(store, project_id)
    structure = store.load_book_structure(project_id)
    labels = structure.printed_labels_by_page()
    furniture = {} if include_page_furniture else project_furniture(store, project_id)
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
    pair_positions: dict[int, tuple[dict[int, int], int]] = {}
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
            confirmation = confirmations.get(page.page_index)
            if confirmation is not None and confirmation.verification_id is not None:
                if (
                    confirmation.verification_id != verification_id
                    or confirmation.raster_sha256 != page.page_asset_sha256
                ):
                    confirmation = None
                else:
                    try:
                        evidence = page.asset_evidence()
                        if evidence is None:
                            raise PageAssetError("recorded raster evidence is missing")
                        verify_page_asset(
                            Path(page.source_image), evidence, what=f"page {page.page_index}"
                        )
                    except PageAssetError as exc:
                        raise PageImageError(str(exc), page_index=page.page_index) from exc
            if confirmation is not None:
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
        current_confirmation = confirmations.get(page.page_index)
        if current_confirmation is not None and current_confirmation.verification_id is not None:
            confirmations.pop(page.page_index)
        page_furniture = furniture.get(page.page_index)
        primary_result = store.load_ocr_run(record.primary_run_id)
        secondary_result = store.load_ocr_run(record.secondary_run_id)
        lines: list[str] = []
        boxes: list[BoundingBox | None] = []
        roles: list[LineRole] = []
        # Reading anchors are aligned-pair indexes; convert them to positions in
        # the exported line stream, skipping furniture and empty pairs.
        pair_line_positions: dict[int, int] = {}
        for pair_index in range(len(record.alignment.pairs)):
            pair_line_positions[pair_index] = len(lines)
            pair = record.alignment.pairs[pair_index]
            furniture_reason = pair_furniture_reason(page_furniture, pair)
            decision = store.latest_decision_for_verification(
                project_id, page.page_index, pair_index, verification_id=verification_id
            )
            if (
                furniture_reason is not None
                and decision is not None
                and not decision_preserves_furniture(
                    decision.selected_text, pair, primary_result, secondary_result
                )
            ):
                furniture_reason = None
            if furniture_reason is not None:
                # A running head is not content: it must not be exported, and its
                # missing decision must not block the book from assembling.
                text = decision.selected_text if decision is not None else ""
                if not text:
                    text = _pair_source_text(store, record, pair)
                dropped.append(
                    DroppedFurniture(
                        page.page_index,
                        text,
                        furniture_reason,
                        pair_index,
                        verification_id,
                        pair.primary_line_indexes,
                        pair.secondary_line_indexes,
                    )
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
                primary_text = _pair_text(primary_result, pair.primary_line_indexes)
                secondary_text = _pair_text(secondary_result, pair.secondary_line_indexes)
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
        pair_positions[page.page_index] = (pair_line_positions, len(lines))

    if unverified_pages or unresolved_pairs or unconfirmed_pages:
        raise UnresolvedContentError(
            unverified_pages=tuple(unverified_pages),
            unresolved_pairs=tuple(unresolved_pairs),
            unconfirmed_pages=tuple(unconfirmed_pages),
            measured_ink=measured_ink,
        )
    exportable = {page.page_index for page in pages}
    images = _page_images(store, project_id, pages, structure, confirmations)
    inline_figures = _inline_figures(store, project_id, pages, pair_positions)
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
        inline_figures=inline_figures,
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

    def __init__(self, message: str, *, page_index: int | None = None) -> None:
        self.page_index = page_index
        super().__init__(message)


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
    assets = {page.page_index: page for page in store.list_pages(project_id)}
    images: list[VerifiedImage] = []
    for page in pages:
        confirmation = confirmations.get(page.page_index)
        role = roles.get(page.page_index, PageRole.UNKNOWN)
        # Only a plate may carry a line of text (its caption); every other page
        # that ships as an image must be textless. A page of prose is re-flowed,
        # never mirrored.
        if page.lines and role is not PageRole.PLATE:
            continue
        if confirmation is not None and confirmation.verification_id is not None:
            if confirmation.kind == "blank":
                continue
            image_role = PageImageRole.FIGURE
            caption = ""
        elif role is PageRole.COVER:
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
        try:
            images.append(_load_image(page, assets[page.page_index], image_role, caption=caption))
        except PageImageError as exc:
            raise PageImageError(str(exc), page_index=page.page_index) from exc
    return tuple(images)


def _inline_figures(
    store: SQLiteStore,
    project_id: int,
    pages: list[VerifiedPage],
    pair_positions: Mapping[int, tuple[dict[int, int], int]],
) -> tuple[VerifiedInlineFigure, ...]:
    """Load human-confirmed crops for mixed text pages (issue #246).

    Only confirmations bound to each page's latest verification are eligible.
    The stored crop bytes are re-hashed; the source page digest must still match
    the confirmation. Model observations without a confirmation are never
    embedded here — export reports them separately as layout warnings.
    """
    from scribeforge.pipeline.figure_review import latest_inline_confirmations

    confirmations = latest_inline_confirmations(store, project_id)
    by_page = {page.page_index: page for page in pages}
    figures: list[VerifiedInlineFigure] = []
    for page_index in sorted(confirmations):
        page = by_page.get(page_index)
        if page is None:
            continue
        positions, line_count = pair_positions.get(page_index, ({}, len(page.lines)))
        source = next(
            (item for item in store.list_pages(project_id) if item.page_index == page_index),
            None,
        )
        if source is None:
            raise PageImageError(
                f"inline figure page {page_index} is missing from the project",
                page_index=page_index,
            )
        evidence = source.asset_evidence()
        if evidence is None:
            raise PageImageError(
                f"page {page_index} has no recorded raster digest for its confirmed figure",
                page_index=page_index,
            )
        try:
            current_digest = hashlib.sha256(Path(source.source_image).read_bytes()).hexdigest()
        except OSError as exc:
            raise PageImageError(
                f"page {page_index} raster is unreadable for its confirmed figure: "
                f"{exc.strerror or exc}",
                page_index=page_index,
            ) from exc
        if (
            current_digest != evidence.sha256
            or evidence.sha256 != confirmations[page_index][0].source_sha256
        ):
            raise PageImageError(
                f"page {page_index} raster changed after the figure was confirmed; "
                "restore the verified source or create a new preview",
                page_index=page_index,
            )
        if not page.lines:
            raise PageImageError(
                f"page {page_index} has a confirmed inline figure but no accepted text; "
                "confirm it as a whole-page plate instead",
                page_index=page_index,
            )
        label = page.printed_label or str(page_index + 1)
        for confirmation in confirmations[page_index]:
            if hashlib.sha256(confirmation.crop_png).hexdigest() != confirmation.crop_sha256:
                raise PageImageError(
                    f"confirmed figure crop on page {page_index} does not match its digest",
                    page_index=page_index,
                )
            anchor = confirmation.anchor_pair_index
            if anchor in positions:
                reading_position = positions[anchor]
            elif anchor >= line_count:
                reading_position = line_count
            else:
                raise PageImageError(
                    f"confirmed figure anchor on page {page_index} is out of range",
                    page_index=page_index,
                )
            figures.append(
                VerifiedInlineFigure(
                    page_index=page_index,
                    file_name=(f"inline-{page_index + 1:06d}-{confirmation.id:06d}.png"),
                    media_type="image/png",
                    sha256=confirmation.crop_sha256,
                    data=confirmation.crop_png,
                    alt=f"第 {label} 页的插图",
                    caption=confirmation.caption,
                    reading_position=reading_position,
                    confirmation_id=confirmation.id,
                    anchor_pair_index=anchor,
                    run_id=confirmation.run_id,
                    block_index=confirmation.block_index,
                )
            )
    figures.sort(key=lambda item: (item.page_index, item.reading_position, item.confirmation_id))
    return tuple(figures)


def _load_image(
    page: VerifiedPage, source: PageRecord, role: PageImageRole, caption: str = ""
) -> VerifiedImage:
    """Read one page raster and record its digest, or refuse with the reason."""

    path = Path(source.source_image)
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
            f"page {page.page_index} must be embedded as an image, but its raster {path} is empty"
        )
    actual_sha256 = hashlib.sha256(data).hexdigest()
    try:
        evidence = source.asset_evidence()
    except PageAssetError as exc:
        raise PageImageError(str(exc)) from exc
    if evidence is not None and actual_sha256 != evidence.sha256:
        raise PageImageError(
            f"page {page.page_index} image does not match the recorded page raster digest; "
            "restore it from a verified backup or re-import the project"
        )
    if evidence is not None and len(data) != evidence.byte_size:
        raise PageImageError(f"page {page.page_index} image size does not match recorded evidence")
    label = page.printed_label or str(page.page_index + 1)
    alt = "封面" if role is PageImageRole.COVER else f"第 {label} 页的插图"
    return VerifiedImage(
        page_index=page.page_index,
        file_name=f"page-{page.page_index + 1:06d}{suffix}",
        media_type=media_type,
        role=role,
        sha256=actual_sha256,
        data=data,
        alt=alt,
        caption=caption,
    )


def _pair_text(result: PageOCRResult, indexes: tuple[int, ...]) -> str | None:
    if not indexes:
        return None
    return assemble_group_text(result.lines[index].text for index in indexes)


def _pair_is_empty(primary: PageOCRResult, secondary: PageOCRResult, pair: LineAlignment) -> bool:
    """True when neither engine returned any text for this pair."""

    texts = [
        result.lines[index].text
        for result, indexes in (
            (primary, pair.primary_line_indexes),
            (secondary, pair.secondary_line_indexes),
        )
        for index in indexes
    ]
    return bool(texts) and all(not text.strip() for text in texts)


def _pair_box(
    primary: PageOCRResult, secondary: PageOCRResult, pair: LineAlignment
) -> BoundingBox | None:
    """Union of every cited source box, for layout reconstruction and crops."""

    boxes = [primary.lines[index].box for index in pair.primary_line_indexes]
    boxes.extend(secondary.lines[index].box for index in pair.secondary_line_indexes)
    if not boxes:
        return None
    left = min(box.x for box in boxes)
    top = min(box.y for box in boxes)
    right = max(box.x + box.width for box in boxes)
    bottom = max(box.y + box.height for box in boxes)
    return BoundingBox(left, top, right - left, bottom - top)


def _pair_role(primary: PageOCRResult, secondary: PageOCRResult, pair: LineAlignment) -> LineRole:
    """Block label of this pair, mapped onto an export role.

    Both engines are consulted: a title one engine labelled and the other did not
    is still a title, and a footnote that only one engine boxed must not be
    rendered as body text.
    """

    for result, indexes in (
        (primary, pair.primary_line_indexes),
        (secondary, pair.secondary_line_indexes),
    ):
        for index in indexes:
            block = result.block_of_line(index)
            if block is None:
                continue
            if block.block_type is BlockType.TITLE:
                return LineRole.HEADING
            if block.block_type is BlockType.FOOTNOTE:
                return LineRole.FOOTNOTE
    return LineRole.BODY


def _pair_source_text(store: SQLiteStore, record: VerificationRecord, pair: LineAlignment) -> str:
    primary = store.load_ocr_run(record.primary_run_id)
    if pair.primary_line_indexes:
        return assemble_group_text(primary.lines[index].text for index in pair.primary_line_indexes)
    secondary = store.load_ocr_run(record.secondary_run_id)
    return assemble_group_text(secondary.lines[index].text for index in pair.secondary_line_indexes)
