from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scribeforge.domain.crops import review_crop_file_name
from scribeforge.domain.decisions import DecisionActor, adjudicate_pair
from scribeforge.domain.ocr import PageOCRResult
from scribeforge.domain.risk import PairRisk, ReviewCandidate, RiskReason
from scribeforge.storage.sqlite import SQLiteStore, VerificationRecord, VLMReviewRecord


@dataclass(frozen=True, slots=True)
class AdjudicationResult:
    page_index: int
    resolved: tuple[int, ...]
    recorded: int
    unresolved: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ReviewItem:
    page_index: int
    pair_index: int
    score: float
    reasons: tuple[RiskReason, ...]


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
    )


def adjudicate_verification(
    store: SQLiteStore,
    project_id: int,
    verification_id: int,
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
    recorded = 0

    for pair_index, pair in enumerate(record.alignment.pairs):
        if store.latest_decision(project_id, page_index, pair_index) is not None:
            resolved.append(pair_index)
            continue

        reading = readings.get(pair_index)
        adjudication = adjudicate_pair(
            _line_text(primary, pair.primary_line_index),
            _line_text(secondary, pair.secondary_line_index),
            None if reading is None else reading.reading,
        )
        selected_text = adjudication.selected_text
        actor = adjudication.actor
        if selected_text is None or actor is None:
            unresolved.append(pair_index)
            continue

        store.append_decision(
            project_id=project_id,
            page_index=page_index,
            pair_index=pair_index,
            selected_text=selected_text,
            actor=actor.value,
            evidence_refs=_evidence_refs(record, reading),
        )
        resolved.append(pair_index)
        recorded += 1

    return AdjudicationResult(page_index, tuple(resolved), recorded, tuple(unresolved))


def review_queue(store: SQLiteStore, project_id: int) -> tuple[ReviewItem, ...]:
    """List review candidates that still lack an accepted decision."""

    items: list[ReviewItem] = []
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            continue
        record = store.load_verification(verification_id)
        for candidate in record.candidates:
            if store.latest_decision(project_id, page.page_index, candidate.pair_index) is None:
                items.append(
                    ReviewItem(
                        page_index=page.page_index,
                        pair_index=candidate.pair_index,
                        score=candidate.score,
                        reasons=candidate.reasons,
                    )
                )
    return tuple(items)


def review_detail(
    store: SQLiteStore,
    project_id: int,
    page_index: int,
    pair_index: int,
    *,
    crops_dir: Path | None = None,
) -> ReviewDetail:
    """Load the competing evidence for one aligned pair.

    The crop path is only reported when the crop file was actually materialized,
    so a reviewer is never pointed at bytes that do not exist.
    """

    context = _pair_context(store, project_id, page_index, pair_index)
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
