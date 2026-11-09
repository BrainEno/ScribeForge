from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.alignment import (
    AlignmentConflict,
    ConflictKind,
    LineAlignment,
    PageAlignment,
)
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import PairRisk, ReviewCandidate, RiskReason
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.adjudication import (
    adjudicate_verification,
    record_human_decision,
    review_detail,
    review_queue,
)
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
    assert [(decision.selected_text, decision.actor) for decision in decisions] == [
        ("乙", "automatic")
    ]
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


def test_review_detail_exposes_competing_evidence(tmp_path: Path) -> None:
    store, project_id, _, _, _ = _two_engine_page(tmp_path)

    detail = review_detail(store, project_id, 0, 0)

    assert detail.page_index == 0
    assert detail.pair_index == 0
    assert detail.score == 0.5
    assert detail.reasons == (RiskReason.TEXT_DISAGREEMENT,)
    assert detail.primary_text == "鸦"
    assert detail.secondary_text == "雅"
    assert detail.vlm_text is None
    assert detail.vlm_uncertain is False
    assert detail.crop_path is None
    assert detail.accepted_text is None


def test_review_detail_includes_existing_crop_and_reading(tmp_path: Path) -> None:
    store, project_id, verification_id, _, _ = _two_engine_page(tmp_path)
    store.record_vlm_review(
        verification_id,
        VLMReading(0, 0, "local-vlm", "1.0", "maybe", True),
        crop_path="crops/pair-0.png",
        crop_sha256="c" * 64,
    )
    crops_dir = tmp_path / "crops"
    crops_dir.mkdir()
    crop_file = crops_dir / "page-000001-pair-0000.png"
    crop_file.write_bytes(b"crop")

    detail = review_detail(store, project_id, 0, 0, crops_dir=crops_dir)

    assert detail.vlm_text == "maybe"
    assert detail.vlm_uncertain is True
    assert detail.crop_path == str(crop_file)


def test_review_detail_omits_crop_that_was_not_materialized(tmp_path: Path) -> None:
    store, project_id, _, _, _ = _two_engine_page(tmp_path)

    detail = review_detail(store, project_id, 0, 0, crops_dir=tmp_path / "crops")

    assert detail.crop_path is None


def test_review_detail_reports_accepted_text_after_decision(tmp_path: Path) -> None:
    store, project_id, _, _, _ = _two_engine_page(tmp_path)

    record_human_decision(store, project_id, 0, 0, "鸦")

    assert review_detail(store, project_id, 0, 0).accepted_text == "鸦"


def test_review_detail_rejects_unknown_pair(tmp_path: Path) -> None:
    store, project_id, _, _, _ = _two_engine_page(tmp_path)

    with pytest.raises(KeyError, match="unknown pair"):
        review_detail(store, project_id, 0, 99)


def test_review_detail_rejects_page_without_verification(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)

    with pytest.raises(KeyError, match="unknown verification"):
        review_detail(store, project_id, 0, 0)


def test_record_human_decision_cites_verification_engines_and_reading(tmp_path: Path) -> None:
    store, project_id, verification_id, primary_id, secondary_id = _two_engine_page(tmp_path)
    review_id = store.record_vlm_review(
        verification_id,
        VLMReading(0, 0, "local-vlm", "1.0", "maybe", True),
        crop_path="crops/pair-0.png",
        crop_sha256="d" * 64,
    )

    decision_id = record_human_decision(store, project_id, 0, 0, "鸦")

    latest = store.latest_decision(project_id, 0, 0)
    assert latest is not None
    assert latest.id == decision_id
    assert latest.selected_text == "鸦"
    assert latest.actor == "human"
    assert latest.evidence_refs == (
        f"verification_run:{verification_id}",
        f"vlm_review:{review_id}",
        f"engine_run:{primary_id}",
        f"engine_run:{secondary_id}",
    )


def test_human_decision_clears_the_review_queue(tmp_path: Path) -> None:
    store, project_id, verification_id, _, _ = _two_engine_page(tmp_path)
    adjudicate_verification(store, project_id, verification_id)
    assert [(item.page_index, item.pair_index) for item in review_queue(store, project_id)] == [
        (0, 0)
    ]

    record_human_decision(store, project_id, 0, 0, "鸦")

    assert review_queue(store, project_id) == ()


def test_re_adjudication_keeps_a_human_decision(tmp_path: Path) -> None:
    store, project_id, verification_id, _, _ = _two_engine_page(tmp_path)
    record_human_decision(store, project_id, 0, 0, "鸦")

    result = adjudicate_verification(store, project_id, verification_id)

    assert result.recorded == 1
    assert result.resolved == (0, 1)
    latest = store.latest_decision(project_id, 0, 0)
    assert latest is not None
    assert latest.selected_text == "鸦"
    assert latest.actor == "human"


def _agreeing_page(
    tmp_path: Path,
    text: str,
    *,
    risk: PairRisk | None = None,
) -> tuple[SQLiteStore, int, int]:
    """One page where both engines read exactly the same (possibly bad) line."""

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    primary_id = store.record_ocr(
        project_id, PageOCRResult(0, "primary", "1.0", (_line(text, 0.1),))
    )
    secondary_id = store.record_ocr(
        project_id, PageOCRResult(0, "secondary", "1.0", (_line(text, 0.1),))
    )
    alignment = PageAlignment(0, "primary", "secondary", (LineAlignment(0, 0, 1.0, 0.0, ()),))
    pair_risk = risk if risk is not None else PairRisk(0, 0.0, ())
    verification_id = store.record_verification(
        primary_id, secondary_id, alignment, (pair_risk,), ()
    )
    return store, project_id, verification_id


def test_agreement_does_not_let_a_replacement_character_through(
    tmp_path: Path,
) -> None:
    """Regression for #75: both engines agreeing on U+FFFD is not a transcription.

    The pair used to be accepted automatically because the two readers "agreed",
    the queue then showed nothing to do, and the replacement character went into
    the exported book.
    """

    store, project_id, verification_id = _agreeing_page(
        tmp_path,
        "\ufffd",
        risk=PairRisk(
            0,
            0.6,
            (RiskReason.LOW_CONFIDENCE, RiskReason.UNICODE_ANOMALY),
            min_confidence=0.1,
            content_score=0.6,
        ),
    )

    result = adjudicate_verification(store, project_id, verification_id)

    assert result.recorded == 0
    assert result.resolved == ()
    assert result.unresolved == (0,)
    assert result.blocked == (0,)
    assert store.list_decisions(project_id, 0, 0) == ()
    # The queue must show it: a blocked pair that no command can reach is a dead end.
    assert [(item.page_index, item.pair_index) for item in review_queue(store, project_id)] == [
        (0, 0)
    ]


def test_agreement_at_low_confidence_is_queued_not_accepted(tmp_path: Path) -> None:
    store, project_id, verification_id = _agreeing_page(
        tmp_path,
        "鸦",
        risk=PairRisk(
            0, 0.25, (RiskReason.LOW_CONFIDENCE,), min_confidence=0.1, content_score=0.25
        ),
    )

    result = adjudicate_verification(store, project_id, verification_id)

    assert result.unresolved == (0,)
    assert store.list_decisions(project_id, 0, 0) == ()
    assert [item.pair_index for item in review_queue(store, project_id)] == [0]


def test_agreement_at_reported_high_confidence_is_still_accepted(tmp_path: Path) -> None:
    """The gate must not turn the pipeline into a manual transcription tool."""

    store, project_id, verification_id = _agreeing_page(
        tmp_path, "鸦", risk=PairRisk(0, 0.0, (), min_confidence=0.95, content_score=0.0)
    )

    result = adjudicate_verification(store, project_id, verification_id)

    assert result.recorded == 1
    assert store.latest_decision(project_id, 0, 0).selected_text == "鸦"
    assert review_queue(store, project_id) == ()


def test_pairs_where_both_engines_returned_nothing_are_not_decisions(
    tmp_path: Path,
) -> None:
    """An empty line from both readers is not content (issue #74).

    Writing a decision for it would put an invented empty paragraph in the export
    and hide the fact that the page may simply have failed to read.
    """

    store, project_id, verification_id = _agreeing_page(tmp_path, "")

    result = adjudicate_verification(store, project_id, verification_id)

    assert result.recorded == 0
    assert result.empty == (0,)
    assert result.unresolved == ()
    assert store.list_decisions(project_id, 0, 0) == ()
    assert review_queue(store, project_id) == ()


def test_blocked_decision_records_the_risk_that_blocked_it(tmp_path: Path) -> None:
    """A human decision must still cite the risk the machine refused to accept."""

    store, project_id, verification_id = _agreeing_page(
        tmp_path,
        "鸚",
        risk=PairRisk(
            0, 0.25, (RiskReason.LOW_CONFIDENCE,), min_confidence=0.1, content_score=0.25
        ),
    )
    adjudicate_verification(store, project_id, verification_id)

    record_human_decision(store, project_id, 0, 0, "鸚")

    decision = store.latest_decision(project_id, 0, 0)
    assert decision.actor == "human"
    assert decision.selected_text == "鸚"
    assert review_queue(store, project_id) == ()


@pytest.mark.parametrize("machine_actor", ["automatic", "vlm"])
def test_adjudication_does_not_supersede_concurrent_human_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, machine_actor: str
) -> None:
    store, project, verification, _, _ = _two_engine_page(tmp_path)
    target = 1
    if machine_actor == "vlm":
        target = 0
        store.record_vlm_review(
            verification,
            VLMReading(0, 0, "vlm", "1", "鸦", False),
            crop_path="crop.png",
            crop_sha256="a" * 64,
        )
    other = SQLiteStore(tmp_path / "project.sqlite3")
    append = store.append_decision

    def concurrent_human(**kwargs):
        if kwargs["pair_index"] == target:
            record_human_decision(other, project, 0, target, "人定文本")
        return append(**kwargs)

    monkeypatch.setattr(store, "append_decision", concurrent_human)
    result = adjudicate_verification(store, project, verification)
    decisions = store.list_decisions(project, 0, target)
    assert [(d.actor, d.selected_text) for d in decisions] == [("human", "人定文本")]
    assert target in result.resolved
    assert result.recorded == (1 if machine_actor == "vlm" else 0)


def test_concurrent_adjudications_do_not_duplicate_machine_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    first, project, verification, _, _ = _two_engine_page(tmp_path)
    second = SQLiteStore(tmp_path / "project.sqlite3")
    barrier = Barrier(2, timeout=10)
    for store in (first, second):
        original = store.append_decision

        def wait_for_other(*, _original=original, **kwargs):
            barrier.wait()
            return _original(**kwargs)

        monkeypatch.setattr(store, "append_decision", wait_for_other)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(adjudicate_verification, store, project, verification)
            for store in (first, second)
        ]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(result.recorded for result in results) == [0, 1]
    decisions = first.list_decisions(project, 0, 1)
    assert [(d.actor, d.selected_text) for d in decisions] == [("automatic", "乙")]


def test_human_decision_still_supersedes_existing_machine_decision(tmp_path: Path) -> None:
    store, project, verification, _, _ = _two_engine_page(tmp_path)
    result = adjudicate_verification(store, project, verification)
    assert result.recorded == 1
    prior = store.list_decisions(project, 0, 1)
    record_human_decision(store, project, 0, 1, "人工订正")
    decisions = store.list_decisions(project, 0, 1)
    assert decisions[0] == prior[0]
    assert [(d.actor, d.selected_text) for d in decisions] == [
        ("automatic", "乙"),
        ("human", "人工订正"),
    ]
    assert store.latest_decision(project, 0, 1).selected_text == "人工订正"


def _repeat_verification(store: SQLiteStore, verification_id: int) -> int:
    old = store.load_verification(verification_id)
    return store.record_verification(
        old.primary_run_id, old.secondary_run_id, old.alignment, old.risks, old.candidates
    )


def test_reverification_refuses_to_reinterpret_existing_decisions(tmp_path: Path) -> None:
    store, project, verification, _, _ = _two_engine_page(tmp_path)
    assert adjudicate_verification(store, project, verification).recorded == 1
    existing = store.list_decisions(project, 0, 1)
    with pytest.raises(
        ValueError, match="(?i)(verification|decision).*(decision|project|reimport)"
    ):
        _repeat_verification(store, verification)
    assert store.latest_verification_id(project, 0) == verification
    assert store.list_decisions(project, 0, 1) == existing


def test_adjudication_refuses_an_old_verification_after_newer_one(tmp_path: Path) -> None:
    store, project, old, _, _ = _two_engine_page(tmp_path)
    new = _repeat_verification(store, old)
    assert new != old
    with pytest.raises(ValueError, match="(?i)verification.*(latest|newer|changed)"):
        adjudicate_verification(store, project, old)
    assert store.latest_decision(project, 0, 1) is None
    result = adjudicate_verification(store, project, new)
    assert result.recorded == 1
    assert f"verification_run:{new}" in store.latest_decision(project, 0, 1).evidence_refs


@pytest.mark.parametrize("actor", ["automatic", "human"])
def test_new_verification_during_decision_write_requires_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actor: str
) -> None:
    store, project, old, _, _ = _two_engine_page(tmp_path)
    append = store.append_decision
    other = SQLiteStore(tmp_path / "project.sqlite3")
    new_ids: list[int] = []

    def add_verification(**kwargs):
        new_ids.append(_repeat_verification(other, old))
        return append(**kwargs)

    monkeypatch.setattr(store, "append_decision", add_verification)
    target = 1 if actor == "automatic" else 0
    with pytest.raises(ValueError, match="(?i)verification.*(latest|newer|changed)"):
        if actor == "automatic":
            adjudicate_verification(store, project, old)
        else:
            record_human_decision(store, project, 0, target, "人工文本")
    assert store.latest_decision(project, 0, target) is None
    assert store.latest_verification_id(project, 0) == new_ids[0]
    monkeypatch.setattr(store, "append_decision", append)
    if actor == "automatic":
        adjudicate_verification(store, project, new_ids[0])
    else:
        record_human_decision(store, project, 0, target, "人工文本")
    assert (
        f"verification_run:{new_ids[0]}" in store.latest_decision(project, 0, target).evidence_refs
    )


def test_concurrent_new_verification_and_decision_are_serialized(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store, project, old, _, _ = _two_engine_page(tmp_path)
    page = store.list_pages(project)[0]
    other = SQLiteStore(tmp_path / "project.sqlite3")
    barrier = Barrier(2, timeout=10)

    def add_new() -> tuple[str, int | str]:
        barrier.wait()
        try:
            return "verification", _repeat_verification(other, old)
        except ValueError as exc:
            return "rejected_verification", str(exc)

    def decide() -> tuple[str, int | str]:
        barrier.wait()
        try:
            return "decision", store.append_decision(
                project_id=project,
                page_index=0,
                pair_index=1,
                selected_text="乙",
                actor="automatic",
                evidence_refs=(f"verification_run:{old}",),
                expected_page=page,
                expected_verification_id=old,
                require_undecided=True,
            )
        except ValueError as exc:
            return "rejected_decision", str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        new_task = pool.submit(add_new)
        decision_task = pool.submit(decide)
        new_result = new_task.result(timeout=20)
        decision_result = decision_task.result(timeout=20)
    assert (new_result[0], decision_result[0]) in {
        ("verification", "rejected_decision"),
        ("rejected_verification", "decision"),
    }
    if new_result[0] == "verification":
        assert store.latest_verification_id(project, 0) == new_result[1]
        assert store.latest_decision(project, 0, 1) is None
    else:
        assert store.latest_verification_id(project, 0) == old
        assert store.latest_decision(project, 0, 1).selected_text == "乙"


def test_decisions_on_other_pages_do_not_block_new_verification(tmp_path: Path) -> None:
    store, project, first, _, _ = _two_engine_page(tmp_path)
    assert adjudicate_verification(store, project, first).recorded == 1
    store.add_page(project, 1, "pages/1.png", 100, 200)
    primary = store.record_ocr(project, PageOCRResult(1, "primary", "1", (_line("甲", 0.1),)))
    secondary = store.record_ocr(project, PageOCRResult(1, "secondary", "1", (_line("甲", 0.1),)))
    alignment = PageAlignment(1, "primary", "secondary", (LineAlignment(0, 0, 1.0, 0.0, ()),))
    second = store.record_verification(primary, secondary, alignment, (PairRisk(0, 0.0, ()),), ())
    assert store.latest_verification_id(project, 1) == second
    assert store.latest_decision(project, 0, 1).selected_text == "乙"


def _historical_batches(tmp_path: Path) -> tuple[SQLiteStore, int, int, int]:
    """Produce a state older versions allowed: same pair index, different lines."""
    store, project, old, primary, secondary = _two_engine_page(tmp_path)
    original = store.load_verification(old)
    reversed_alignment = PageAlignment(
        0, "primary", "secondary", tuple(reversed(original.alignment.pairs))
    )
    new = store.record_verification(
        primary,
        secondary,
        reversed_alignment,
        (PairRisk(0, 0.0, ()), PairRisk(1, 0.5, (RiskReason.TEXT_DISAGREEMENT,))),
        (),
    )
    return store, project, old, new


@pytest.mark.parametrize("path", ["assembly", "review", "detail", "progress", "repair"])
def test_historical_decision_cannot_be_reinterpreted_as_new_pair(tmp_path: Path, path: str) -> None:
    from scribeforge.pipeline.assembly import assemble_verified_content
    from scribeforge.pipeline.rounds import open_pairs, progress

    store, project, old, new = _historical_batches(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="乙",
        actor="human",
        evidence_refs=(f"verification_run:{new}",),
    )
    # This was pair 1 (乙) in the old run; pair 1 now means 鸦/雅.
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=1,
        selected_text="乙",
        actor="human",
        evidence_refs=(f"verification_run:{old}",),
    )
    with pytest.raises(ValueError, match="(?i)(ambiguous|verification).*(decision|batch)"):
        if path == "assembly":
            assemble_verified_content(store, project, include_page_furniture=True)
        elif path == "review":
            review_queue(store, project, include_page_furniture=True)
        elif path == "detail":
            review_detail(store, project, 0, 1)
        elif path == "progress":
            progress(store, project, include_page_furniture=True)
        else:
            open_pairs(store, project, include_page_furniture=True)
    # Raw evidence remains accessible for an audit and is not rewritten.
    assert store.list_decisions(project, 0, 1)[0].selected_text == "乙"
    assert store.load_verification(old).alignment.pairs[1].primary_line_index == 1


@pytest.mark.parametrize("refs", [(), ("engine_run:1",), ("verification_run:bad",)])
def test_multibatch_decision_without_provable_binding_is_rejected(
    tmp_path: Path, refs: tuple[str, ...]
) -> None:
    store, project, _, _ = _historical_batches(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="乙",
        actor="human",
        evidence_refs=refs,
    )
    with pytest.raises(ValueError, match="(?i)(ambiguous|verification).*(decision|batch)"):
        store.latest_verification_id(project, 0)


def test_multibatch_matching_latest_decisions_and_revisions_are_usable(tmp_path: Path) -> None:
    store, project, old, new = _historical_batches(tmp_path)
    assert store.latest_verification_id(project, 0) == new  # no decisions
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="历史",
        actor="human",
        evidence_refs=(f"verification_run:{old}",),
    )
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="乙",
        actor="human",
        evidence_refs=(f"verification_run:{new}",),
    )
    assert store.latest_verification_id(project, 0) == new
    assert review_detail(store, project, 0, 0).accepted_text == "乙"
    assert len(store.list_decisions(project, 0, 0)) == 2


def test_multibatch_conflicting_verification_refs_rejected(tmp_path: Path) -> None:
    store, project, old, new = _historical_batches(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="乙",
        actor="human",
        evidence_refs=(f"verification_run:{new}", f"verification_run:{old}"),
    )
    with pytest.raises(ValueError, match="(?i)(ambiguous|verification).*(decision|batch)"):
        store.latest_verification_id(project, 0)


def test_multibatch_orphan_pair_rejected_even_if_it_cites_latest(tmp_path: Path) -> None:
    store, project, _, new = _historical_batches(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=9,
        selected_text="不可归属",
        actor="human",
        evidence_refs=(f"verification_run:{new}",),
    )
    with pytest.raises(ValueError, match="(?i)(ambiguous|verification).*(decision|batch)"):
        store.latest_verification_id(project, 0)


def test_single_batch_legacy_uncited_decision_remains_compatible(tmp_path: Path) -> None:
    store, project, only, _, _ = _two_engine_page(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="旧决定",
        actor="human",
        evidence_refs=(),
    )
    assert store.latest_verification_id(project, 0) == only
    assert review_detail(store, project, 0, 0).accepted_text == "旧决定"


def test_human_cannot_append_on_ambiguous_history(tmp_path: Path) -> None:
    store, project, old, _ = _historical_batches(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=1,
        selected_text="乙",
        actor="human",
        evidence_refs=(f"verification_run:{old}",),
    )
    with pytest.raises(ValueError, match="ambiguous verification batch"):
        record_human_decision(store, project, 0, 1, "重新审阅")
    assert len(store.list_decisions(project, 0, 1)) == 1


@pytest.mark.parametrize(
    "refs",
    [
        (),
        ("verification_run:999",),
        ("verification_run:bad",),
        ("verification_run:999", "verification_run:1"),
    ],
)
def test_guarded_decision_requires_exact_verification_citation(
    tmp_path: Path, refs: tuple[str, ...]
) -> None:
    store, project, verification, _, _ = _two_engine_page(tmp_path)
    with pytest.raises(ValueError, match="(?i)verification.*(citation|reference)"):
        store.append_decision(
            project_id=project,
            page_index=0,
            pair_index=0,
            selected_text="鸦",
            actor="human",
            evidence_refs=refs,
            expected_verification_id=verification,
        )
    assert store.list_decisions(project, 0, 0) == ()
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="鸦",
        actor="human",
        evidence_refs=(f"verification_run:{verification}",),
        expected_verification_id=verification,
    )
    assert len(store.list_decisions(project, 0, 0)) == 1


@pytest.mark.parametrize("kind", ["wrong", "conflicting", "orphan", "malformed"])
def test_single_batch_explicitly_invalid_decision_cannot_be_used(tmp_path: Path, kind: str) -> None:
    import sqlite3

    from scribeforge.pipeline.assembly import assemble_verified_content
    from scribeforge.project_audit import audit_project_store

    store, project, only, _, _ = _two_engine_page(tmp_path)
    store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=0,
        selected_text="鸦",
        actor="human",
        evidence_refs=(f"verification_run:{only}",),
    )
    refs = (f"verification_run:{only}",) if kind == "orphan" else ("verification_run:999",)
    if kind == "conflicting":
        refs = (f"verification_run:{only}", "verification_run:999")
    pair = 9 if kind == "orphan" else 1
    decision = store.append_decision(
        project_id=project,
        page_index=0,
        pair_index=pair,
        selected_text="乙",
        actor="human",
        evidence_refs=refs,
    )
    if kind == "malformed":
        with sqlite3.connect(tmp_path / "project.sqlite3") as db:
            db.execute("UPDATE decisions SET evidence_refs_json='{' WHERE id=?", (decision,))
    with pytest.raises(ValueError, match="ambiguous verification batch"):
        assemble_verified_content(store, project, include_page_furniture=True)
    report = audit_project_store(tmp_path)
    assert any(
        f.code == "decision_verification_ambiguous" and f"decision {decision}" in f.message
        for f in report.findings
    )
