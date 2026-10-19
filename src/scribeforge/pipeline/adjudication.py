from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from scribeforge.domain.decisions import DecisionActor, adjudicate_pair
from scribeforge.domain.ocr import PageOCRResult
from scribeforge.domain.risk import RiskReason
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
    actor: DecisionActor,
) -> tuple[str, ...]:
    refs = [f"verification_run:{record.id}"]
    if actor is DecisionActor.VLM and reading is not None:
        refs.append(f"vlm_review:{reading.id}")
    refs.append(f"engine_run:{record.primary_run_id}")
    refs.append(f"engine_run:{record.secondary_run_id}")
    return tuple(refs)


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
            evidence_refs=_evidence_refs(record, reading, actor),
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
