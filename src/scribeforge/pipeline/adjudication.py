from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scribeforge.domain.acceptance import DEFAULT_POLICY, AcceptancePolicy
from scribeforge.domain.alignment import AlignmentConflict, LineAlignment
from scribeforge.domain.crops import review_crop_file_name
from scribeforge.domain.decisions import DecisionActor, adjudicate_pair
from scribeforge.domain.ocr import BoundingBox, PageOCRResult
from scribeforge.domain.risk import PairRisk, ReviewCandidate, RiskReason
from scribeforge.storage.sqlite import SQLiteStore, VerificationRecord, VLMReviewRecord


class _Unset:
    __slots__ = ()


_FURNITURE_REASON_UNSET = _Unset()


@dataclass(frozen=True, slots=True)
class AdjudicationResult:
    page_index: int
    resolved: tuple[int, ...]
    recorded: int
    unresolved: tuple[int, ...]
    #: Pairs a rule was willing to accept but the acceptance gate refused: text a
    #: machine must not certify (a replacement character, too low confidence, or a
    #: risk score above the policy ceiling). They stay in the review queue.
    blocked: tuple[int, ...] = ()
    #: Pairs where both engines reported nothing but an empty line. They are not
    #: content, so they neither become decisions nor block the page.
    empty: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewItem:
    page_index: int
    pair_index: int
    score: float
    reasons: tuple[RiskReason, ...]
    furniture_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewDetail:
    """Everything a human needs to judge one page pair from persisted evidence."""

    page_index: int
    pair_index: int
    score: float
    reasons: tuple[RiskReason, ...]
    primary_text: str | None
    secondary_text: str | None
    vlm_text: str | None
    vlm_uncertain: bool
    crop_path: str | None
    accepted_text: str | None
    source_box: BoundingBox | None = None
    primary_box: BoundingBox | None = None
    secondary_box: BoundingBox | None = None
    conflicts: tuple[AlignmentConflict, ...] = ()
    furniture_reason: str | None = None


def _line_box(result: PageOCRResult, index: int | None) -> BoundingBox | None:
    return None if index is None else result.lines[index].box


def _merge_boxes(
    primary: BoundingBox | None, secondary: BoundingBox | None
) -> BoundingBox | None:
    if primary is None:
        return secondary
    if secondary is None:
        return primary
    left = min(primary.x, secondary.x)
    top = min(primary.y, secondary.y)
    right = max(primary.x + primary.width, secondary.x + secondary.width)
    bottom = max(primary.y + primary.height, secondary.y + secondary.height)
    return BoundingBox(left, top, right - left, bottom - top)


def _line_text(result: PageOCRResult, index: int | None) -> str | None:
    return None if index is None else result.lines[index].text


def _latest_readings(
    readings: Sequence[VLMReviewRecord],
) -> dict[int, VLMReviewRecord]:
    """Keep the newest literal reading per pair (append-only evidence)."""

    latest: dict[int, VLMReviewRecord] = {}
    for reading in readings:
        latest[reading.pair_index] = reading
    return latest


def _evidence_refs(
    record: VerificationRecord,
    reading: VLMReviewRecord | None,
) -> tuple[str, ...]:
    """Cite the immutable evidence a decision was accepted from."""

    refs = [f"verification_run:{record.id}"]
    if reading is not None:
        refs.append(f"vlm_review:{reading.id}")
    refs.append(f"engine_run:{record.primary_run_id}")
    refs.append(f"engine_run:{record.secondary_run_id}")
    return tuple(refs)


@dataclass(frozen=True, slots=True)
class _PairContext:
    record: VerificationRecord
    primary_text: str | None
    secondary_text: str | None
    reading: VLMReviewRecord | None
    candidate: ReviewCandidate | None
    risk: PairRisk
    primary_box: BoundingBox | None
    secondary_box: BoundingBox | None
    conflicts: tuple[AlignmentConflict, ...]


def _pair_context(
    store: SQLiteStore, project_id: int, page_index: int, pair_index: int
) -> _PairContext:
    """Load one aligned pair from the newest verification run of a page."""

    verification_id = store.latest_verification_id(project_id, page_index)
    record = store.load_verification(verification_id)
    if not 0 <= pair_index < len(record.alignment.pairs):
        raise KeyError(f"unknown pair {pair_index} on page {page_index}")

    pair = record.alignment.pairs[pair_index]
    primary = store.load_ocr_run(record.primary_run_id)
    secondary = store.load_ocr_run(record.secondary_run_id)
    candidate = next(
        (item for item in record.candidates if item.pair_index == pair_index), None
    )
    return _PairContext(
        record=record,
        primary_text=_line_text(primary, pair.primary_line_index),
        secondary_text=_line_text(secondary, pair.secondary_line_index),
        reading=_latest_readings(store.list_vlm_reviews(verification_id)).get(pair_index),
        candidate=candidate,
        risk=record.risks[pair_index],
        primary_box=_line_box(primary, pair.primary_line_index),
        secondary_box=_line_box(secondary, pair.secondary_line_index),
        conflicts=pair.conflicts,
    )


def adjudicate_verification(
    store: SQLiteStore,
    project_id: int,
    verification_id: int,
    *,
    policy: AcceptancePolicy = DEFAULT_POLICY,
) -> AdjudicationResult:
    """Resolve a verification run's pairs into the append-only decision layer.

    Idempotent: pairs that already have a decision are left untouched, so an
    interrupted adjudication can be retried without duplicating decisions. Every
    accepted decision cites the verification run, both OCR engine runs, and the
    VLM reading when one was used. Pairs that cannot be accepted from evidence
    stay unresolved for human review.
    """

    record = store.load_verification(verification_id)
    page_index = record.alignment.page_index
    primary = store.load_ocr_run(record.primary_run_id)
    secondary = store.load_ocr_run(record.secondary_run_id)
    readings = _latest_readings(store.list_vlm_reviews(verification_id))

    resolved: list[int] = []
    unresolved: list[int] = []
    blocked: list[int] = []
    empty: list[int] = []
    recorded = 0

    for pair_index, pair in enumerate(record.alignment.pairs):
        if store.latest_decision(project_id, page_index, pair_index) is not None:
            resolved.append(pair_index)
            continue

        primary_text = _line_text(primary, pair.primary_line_index)
        secondary_text = _line_text(secondary, pair.secondary_line_index)
        if _blank_pair(primary_text, secondary_text):
            # Two readers that both returned an empty line agree on nothing at all.
            # Accepting "" would put an invented empty paragraph in the export and
            # (worse) let a page that simply failed to OCR look like a blank leaf.
            empty.append(pair_index)
            continue

        reading = readings.get(pair_index)
        risk = record.risks[pair_index] if pair_index < len(record.risks) else None
        adjudication = adjudicate_pair(
            primary_text,
            secondary_text,
            None if reading is None else reading.reading,
            risk=risk,
            policy=policy,
        )
        selected_text = adjudication.selected_text
        actor = adjudication.actor
        if selected_text is None or actor is None:
            if adjudication.blocked:
                blocked.append(pair_index)
            unresolved.append(pair_index)
            continue

        store.append_decision(
            project_id=project_id,
            page_index=page_index,
            pair_index=pair_index,
            selected_text=selected_text,
            actor=actor.value,
            evidence_refs=_evidence_refs(record, reading),
            risk_score=None if risk is None else risk.score,
            risk_reasons=tuple(reason.value for reason in risk.reasons)
            if risk is not None
            else (),
        )
        resolved.append(pair_index)
        recorded += 1

    return AdjudicationResult(
        page_index,
        tuple(resolved),
        recorded,
        tuple(unresolved),
        blocked=tuple(blocked),
        empty=tuple(empty),
    )


def _blank_pair(primary_text: str | None, secondary_text: str | None) -> bool:
    """True when neither reader produced any text for this pair."""

    return all(text is not None and not text.strip() for text in (primary_text, secondary_text))


def review_queue(
    store: SQLiteStore,
    project_id: int,
    *,
    include_page_furniture: bool = False,
) -> tuple[ReviewItem, ...]:
    """List every pair that still lacks an accepted decision.

    The queue is defined by *what is missing*, not by which pairs happened to clear
    the review-candidate score threshold. That distinction matters: a pair the
    acceptance gate refused for low confidence can score below the candidate
    threshold, and a queue built from candidates would then report "0 queued" while
    export stays blocked — a dead end with nothing to click (issue #75).

    Running heads and folios are excluded by default (they are not content), and
    so are pairs where both engines returned an empty line (there is nothing to
    transcribe; confirmed-blank handling covers the page, see issue #74). Set
    ``include_page_furniture`` to review those pairs explicitly. Book-wide
    furniture detection is why this cannot be computed one page at a time.
    """

    furniture = project_furniture(store, project_id)
    items: list[ReviewItem] = []
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            continue
        record = store.load_verification(verification_id)
        primary = store.load_ocr_run(record.primary_run_id)
        secondary = store.load_ocr_run(record.secondary_run_id)
        page_furniture = furniture.get(page.page_index, {})
        for pair_index, pair in enumerate(record.alignment.pairs):
            furniture_reason = _pair_furniture_reason(page_furniture, pair)
            if furniture_reason is not None and not include_page_furniture:
                continue
            if store.latest_decision(project_id, page.page_index, pair_index) is not None:
                continue
            if _blank_pair(
                _line_text(primary, pair.primary_line_index),
                _line_text(secondary, pair.secondary_line_index),
            ):
                continue
            risk = record.risks[pair_index]
            items.append(
                ReviewItem(
                    page_index=page.page_index,
                    pair_index=pair_index,
                    score=risk.score,
                    reasons=risk.reasons,
                    furniture_reason=furniture_reason,
                )
            )
    items.sort(key=lambda item: (-item.score, item.page_index, item.pair_index))
    return tuple(items)


def _pair_furniture_reason(
    page_furniture: Mapping[int, str], pair: LineAlignment
) -> str | None:
    # Furniture indexes are derived from the primary reading. Secondary line
    # indexes are a separate coordinate space and may collide with body indexes.
    index = pair.primary_line_index
    if index is not None and index in page_furniture:
        return page_furniture[index]
    return None


def project_furniture(
    store: SQLiteStore, project_id: int
) -> Mapping[int, Mapping[int, str]]:
    """Book-wide furniture detection from stored OCR runs.

    Public because every stage that walks pairs must agree on which ones are page
    furniture: a repair round that re-read a running head would spend a reader on
    text the export is going to drop.
    """

    from scribeforge.domain.furniture import detect_page_furniture

    pages: dict[int, PageOCRResult] = {}
    for page in store.list_pages(project_id):
        for engine in ("primary", "secondary"):
            try:
                run_id = store.latest_engine_run(project_id, page.page_index, engine)
            except KeyError:
                continue
            pages[page.page_index] = store.load_ocr_run(run_id)
            break
    detected = detect_page_furniture(pages)
    return {page_index: dict(found) for page_index, found in detected.items()}


def review_detail(
    store: SQLiteStore,
    project_id: int,
    page_index: int,
    pair_index: int,
    *,
    crops_dir: Path | None = None,
    furniture_reason: str | None | _Unset = _FURNITURE_REASON_UNSET,
) -> ReviewDetail:
    """Load the competing evidence for one aligned pair.

    The crop path is only reported when the crop file was actually materialized,
    so a reviewer is never pointed at bytes that do not exist. Furniture is
    classified from the book-wide evidence unless a caller supplies the already
    computed reason (including an explicit ``None`` for a known non-furniture pair).
    """

    context = _pair_context(store, project_id, page_index, pair_index)
    if isinstance(furniture_reason, _Unset):
        pair = context.record.alignment.pairs[pair_index]
        furniture_reason = _pair_furniture_reason(
            project_furniture(store, project_id).get(page_index, {}), pair
        )
    candidate = context.candidate
    decision = store.latest_decision(project_id, page_index, pair_index)
    reading = context.reading
    return ReviewDetail(
        page_index=page_index,
        pair_index=pair_index,
        score=candidate.score if candidate is not None else context.risk.score,
        reasons=candidate.reasons if candidate is not None else context.risk.reasons,
        primary_text=context.primary_text,
        secondary_text=context.secondary_text,
        vlm_text=None if reading is None else reading.reading.text,
        vlm_uncertain=reading is not None and reading.reading.uncertain,
        crop_path=_materialized_crop(crops_dir, page_index, pair_index, candidate),
        accepted_text=None if decision is None else decision.selected_text,
        source_box=(
            candidate.crop
            if candidate is not None
            else _merge_boxes(context.primary_box, context.secondary_box)
        ),
        primary_box=context.primary_box,
        secondary_box=context.secondary_box,
        conflicts=context.conflicts,
        furniture_reason=furniture_reason,
    )


def record_human_decision(
    store: SQLiteStore,
    project_id: int,
    page_index: int,
    pair_index: int,
    text: str,
) -> int:
    """Append a human-accepted decision for one pair, citing its evidence.

    Source OCR is never rewritten: the decision becomes the pair's newest entry
    in the append-only decision layer, superseding any earlier automatic or VLM
    acceptance while leaving that earlier row intact.
    """

    context = _pair_context(store, project_id, page_index, pair_index)
    return store.append_decision(
        project_id=project_id,
        page_index=page_index,
        pair_index=pair_index,
        selected_text=text,
        actor=DecisionActor.HUMAN.value,
        evidence_refs=_evidence_refs(context.record, context.reading),
    )


def _materialized_crop(
    crops_dir: Path | None,
    page_index: int,
    pair_index: int,
    candidate: ReviewCandidate | None,
) -> str | None:
    if crops_dir is None or candidate is None:
        return None
    path = crops_dir / review_crop_file_name(page_index, pair_index)
    return str(path) if path.exists() else None
