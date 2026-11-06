"""Column-level structural ambiguity must survive storage and block consensus."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scribeforge.domain.alignment import (
    AlignmentStatus,
    AmbiguityReason,
    LineAlignment,
    PageAlignment,
    align_pages,
)
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.risk import PairRisk, RiskReason, assess_page, review_candidates
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.adjudication import (
    adjudicate_verification,
    record_human_decision,
    review_detail,
)
from scribeforge.storage.sqlite import SQLiteStore


def _competing_columns() -> tuple[PageOCRResult, PageOCRResult]:
    primary = PageOCRResult(
        0,
        "mineru",
        "fixture",
        (OCRLine("ambiguous body", BoundingBox(0.30, 0.20, 0.40, 0.05)),),
    )
    secondary = PageOCRResult(
        0,
        "paddleocr",
        "fixture",
        (
            OCRLine("ambiguous body", BoundingBox(0.27, 0.20, 0.30, 0.05)),
            OCRLine("ambiguouz body", BoundingBox(0.43, 0.20, 0.30, 0.05)),
        ),
    )
    return primary, secondary


def _persisted_column_ambiguity(
    tmp_path: Path,
) -> tuple[SQLiteStore, int, int, int, int, PageAlignment]:
    primary, secondary = _competing_columns()
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "page.png", 1000, 1000)
    primary_id = store.record_ocr(project_id, primary)
    secondary_id = store.record_ocr(project_id, secondary)
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary)
    verification_id = store.record_verification(
        primary_id, secondary_id, alignment, risks, candidates
    )
    return store, project_id, verification_id, primary_id, secondary_id, alignment


def test_near_equal_column_mappings_become_structural_alternatives() -> None:
    primary, secondary = _competing_columns()

    result = align_pages(primary, secondary)

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.status is AlignmentStatus.AMBIGUOUS
    assert pair.primary_line_indexes == (0,)
    assert pair.secondary_line_indexes == (0, 1)
    assert pair.ambiguity is not None
    assert pair.ambiguity.reason is AmbiguityReason.COLUMN_MAPPING
    assert pair.ambiguity.score_margin <= 0.05
    assert len(pair.ambiguity.alternatives) == 2
    assert {
        (item.primary_line_indexes, item.secondary_line_indexes)
        for item in pair.ambiguity.alternatives
    } == {((0,), (0,)), ((0,), (1,))}
    assert all(item.score > 0 for item in pair.ambiguity.alternatives)
    assert all(0.0 <= item.text_similarity <= 1.0 for item in pair.ambiguity.alternatives)


def test_column_ambiguity_round_trips_and_cannot_be_vlm_or_machine_accepted(
    tmp_path: Path,
) -> None:
    store, project_id, verification_id, _, _, alignment = _persisted_column_ambiguity(
        tmp_path
    )
    store.record_vlm_review(
        verification_id,
        VLMReading(0, 0, "local-vlm", "fixture", "ambiguous body", False),
        crop_path="crops/pair-0.png",
        crop_sha256="a" * 64,
    )

    restored = store.load_verification(verification_id)
    outcome = adjudicate_verification(store, project_id, verification_id)
    detail = review_detail(store, project_id, 0, 0)

    assert restored.alignment == alignment
    assert restored.risks[0].score == 1.0
    assert RiskReason.ALIGNMENT_AMBIGUITY in restored.risks[0].reasons
    assert outcome.recorded == 0
    assert outcome.blocked == outcome.unresolved == (0,)
    assert store.latest_decision(project_id, 0, 0) is None
    assert detail.alignment_status is AlignmentStatus.AMBIGUOUS
    assert detail.primary_line_indexes == (0,)
    assert detail.secondary_line_indexes == (0, 1)
    assert detail.ambiguity == alignment.pairs[0].ambiguity


def test_column_membership_corruption_fails_closed(tmp_path: Path) -> None:
    store, _, verification_id, _, _, _ = _persisted_column_ambiguity(tmp_path)
    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute(
            "DELETE FROM alignment_line_members WHERE side = 'secondary' AND line_index = 1"
        )
        connection.commit()

    with pytest.raises(ValueError, match="(?i)(member|line|alignment)"):
        store.load_verification(verification_id)


def test_historical_human_decision_is_not_reused_for_a_different_column_pair(
    tmp_path: Path,
) -> None:
    store, project_id, old_verification, primary_id, secondary_id, alignment = (
        _persisted_column_ambiguity(tmp_path)
    )
    record_human_decision(store, project_id, 0, 0, "人工确认的原文")
    old_decision = store.latest_decision(project_id, 0, 0)
    assert old_decision is not None

    # The scalar anchors remain 0 -> 0, but the new pair omits secondary line 1
    # and is no longer the ambiguous region the person actually decided.
    changed = PageAlignment(
        0,
        "mineru",
        "paddleocr",
        (LineAlignment(0, 0, 1.0, 0.0, ()),),
    )
    with pytest.raises(ValueError, match="verification cannot be replaced after decisions"):
        store.record_verification(
            primary_id,
            secondary_id,
            changed,
            (PairRisk(0, 0.0, ()),),
            (),
        )

    assert store.latest_verification_id(project_id, 0) == old_verification
    assert store.latest_decision(project_id, 0, 0) == old_decision
    assert store.load_verification(old_verification).alignment == alignment
    assert len(store.list_decisions(project_id, 0, 0)) == 1
