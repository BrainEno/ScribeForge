from __future__ import annotations

from pathlib import Path

from scribeforge.domain.alignment import (
    AlignmentConflict,
    ConflictKind,
    LineAlignment,
    PageAlignment,
)
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import PairRisk, ReviewCandidate, RiskReason
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.adjudication import adjudicate_verification, review_queue
from scribeforge.storage.sqlite import SQLiteStore


def _line(text: str, y: float) -> OCRLine:
    box = BoundingBox(0.1, y, 0.5, 0.05)
    return OCRLine(text, box, (OCRToken(text, 0.9, box),))


def _two_engine_page(tmp_path: Path) -> tuple[SQLiteStore, int, int, int, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)

    primary_id = store.record_ocr(
        project_id,
        PageOCRResult(0, "primary", "1.0", (_line("鸦", 0.1), _line("乙", 0.5))),
    )
    secondary_id = store.record_ocr(
        project_id,
        PageOCRResult(0, "secondary", "1.0", (_line("雅", 0.1), _line("乙", 0.5))),
    )

    alignment = PageAlignment(
        0,
        "primary",
        "secondary",
        (
            LineAlignment(
                0,
                0,
                0.5,
                0.0,
                (AlignmentConflict(ConflictKind.SUBSTITUTION, "鸦", "雅", 0, 1, 0, 1),),
            ),
            LineAlignment(1, 1, 1.0, 0.0, ()),
        ),
    )
    risks = (
        PairRisk(0, 0.5, (RiskReason.TEXT_DISAGREEMENT,)),
        PairRisk(1, 0.0, ()),
    )
    candidates = (
        ReviewCandidate(
            0,
            0,
            0.5,
            (RiskReason.TEXT_DISAGREEMENT,),
            BoundingBox(0.1, 0.1, 0.5, 0.05),
        ),
    )
    verification_id = store.record_verification(
        primary_id, secondary_id, alignment, risks, candidates
    )
    return store, project_id, verification_id, primary_id, secondary_id


def test_adjudication_accepts_agreement_and_defers_disagreement(tmp_path: Path) -> None:
    store, project_id, verification_id, primary_id, secondary_id = _two_engine_page(tmp_path)

    result = adjudicate_verification(store, project_id, verification_id)

    assert result.page_index == 0
    assert result.recorded == 1
    assert result.resolved == (1,)
    assert result.unresolved == (0,)

    decisions = store.list_decisions(project_id, 0, 1)
    assert [(decision.selected_text, decision.actor) for decision in decisions] == [("乙", "automatic")]
    assert decisions[0].evidence_refs == (
        f"verification_run:{verification_id}",
        f"engine_run:{primary_id}",
        f"engine_run:{secondary_id}",
    )


def test_adjudication_uses_vlm_evidence_and_is_idempotent(tmp_path: Path) -> None:
    store, project_id, verification_id, _, _ = _two_engine_page(tmp_path)
    first = adjudicate_verification(store, project_id, verification_id)
    assert first.recorded == 1

    review_id = store.record_vlm_review(
        verification_id,
        VLMReading(0, 0, "local-vlm", "1.0", "鸦", False),
        crop_path="crops/pair-0.png",
        crop_sha256="a" * 64,
    )

    second = adjudicate_verification(store, project_id, verification_id)
    assert second.recorded == 1
    assert second.resolved == (0, 1)
    assert second.unresolved == ()

    decisions = store.list_decisions(project_id, 0, 0)
    assert [(decision.selected_text, decision.actor) for decision in decisions] == [("鸦", "vlm")]
    assert f"vlm_review:{review_id}" in decisions[0].evidence_refs

    third = adjudicate_verification(store, project_id, verification_id)
    assert third.recorded == 0
    assert third.resolved == (0, 1)
    assert third.unresolved == ()


def test_review_queue_lists_only_undecided_candidates(tmp_path: Path) -> None:
    store, project_id, verification_id, _, _ = _two_engine_page(tmp_path)
    adjudicate_verification(store, project_id, verification_id)

    queue = review_queue(store, project_id)

    assert [(item.page_index, item.pair_index) for item in queue] == [(0, 0)]
    assert queue[0].score == 0.5
    assert queue[0].reasons == (RiskReason.TEXT_DISAGREEMENT,)


def test_review_queue_is_empty_once_every_candidate_is_decided(tmp_path: Path) -> None:
    store, project_id, verification_id, _, _ = _two_engine_page(tmp_path)
    store.record_vlm_review(
        verification_id,
        VLMReading(0, 0, "local-vlm", "1.0", "鸦", False),
        crop_path="crops/pair-0.png",
        crop_sha256="b" * 64,
    )
    adjudicate_verification(store, project_id, verification_id)

    assert review_queue(store, project_id) == ()


def test_review_queue_skips_pages_without_a_verification(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)

    assert review_queue(store, project_id) == ()
