from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.alignment import LineAlignment, PageAlignment
from scribeforge.domain.content import VerifiedPage
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import PairRisk, RiskReason
from scribeforge.pipeline.assembly import (
    UnresolvedContentError,
    UnresolvedPair,
    assemble_verified_content,
)
from scribeforge.storage.sqlite import SQLiteStore


def _line(text: str, y: float) -> OCRLine:
    box = BoundingBox(0.1, y, 0.5, 0.05)
    return OCRLine(text, box, (OCRToken(text, 0.9, box),))


def _store(tmp_path: Path, title: str = "Book") -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    return store, store.create_project(title, "book.pdf", "sha256")


def _add_page(
    store: SQLiteStore,
    project_id: int,
    page_index: int,
    texts: tuple[str, ...],
    *,
    undecided: frozenset[int] = frozenset(),
) -> None:
    store.add_page(project_id, page_index, f"pages/{page_index}.png", 100, 200)
    lines = tuple(_line(text, 0.1 + 0.1 * index) for index, text in enumerate(texts))
    primary_id = store.record_ocr(
        project_id, PageOCRResult(page_index, "primary", "1.0", lines)
    )
    secondary_id = store.record_ocr(
        project_id, PageOCRResult(page_index, "secondary", "1.0", lines)
    )
    alignment = PageAlignment(
        page_index,
        "primary",
        "secondary",
        tuple(LineAlignment(index, index, 1.0, 0.0, ()) for index in range(len(texts))),
    )
    risks = tuple(PairRisk(index, 0.0, ()) for index in range(len(texts)))
    store.record_verification(primary_id, secondary_id, alignment, risks, ())
    for index, text in enumerate(texts):
        if index in undecided:
            continue
        store.append_decision(
            project_id=project_id,
            page_index=page_index,
            pair_index=index,
            selected_text=text,
            actor="automatic",
            evidence_refs=(),
        )


def test_assembly_orders_pages_and_pairs(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ("一", "二"))
    _add_page(store, project_id, 1, ("三",))

    book = assemble_verified_content(store, project_id)

    assert book.title == "Book"
    assert book.pages == (
        VerifiedPage(0, ("一", "二")),
        VerifiedPage(1, ("三",)),
    )


def test_assembly_uses_the_newest_decision(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ("原",))
    store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="校",
        actor="human",
        evidence_refs=(),
    )

    book = assemble_verified_content(store, project_id)

    assert book.pages[0].lines == ("校",)


def test_assembly_blocks_a_textless_page_until_it_is_confirmed(tmp_path: Path) -> None:
    """Zero pairs is ambiguous, not evidence of a blank leaf (issue #74).

    Two crashed workers, a mis-decoded raster and a genuinely empty leaf all leave
    the OCR tables looking identical. Export must not guess which one it is.
    """

    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ())
    _add_page(store, project_id, 1, ("一",))

    with pytest.raises(UnresolvedContentError) as excinfo:
        assemble_verified_content(store, project_id)

    assert excinfo.value.unconfirmed_pages == (0,)
    assert excinfo.value.unresolved_pairs == ()


def test_assembly_accepts_a_confirmed_blank_page(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ())
    _add_page(store, project_id, 1, ("一",))
    store.record_page_confirmation(
        project_id=project_id,
        page_index=0,
        kind="blank",
        actor="human",
        reason="empty leaf between sections, no ink on the scan",
    )

    book = assemble_verified_content(store, project_id)

    assert book.pages == (VerifiedPage(0, ()), VerifiedPage(1, ("一",)))
    assert book.confirmed_blank_pages == (0,)


def test_assembly_skips_pairs_where_both_engines_returned_nothing(
    tmp_path: Path,
) -> None:
    """An empty line from both readers is not content, and nobody owes a decision."""

    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ("一", ""), undecided=frozenset({1}))

    book = assemble_verified_content(store, project_id)

    assert book.pages[0].lines == ("一",)
    assert book.empty_pairs == ((0, 1),)


def test_assembly_summarises_who_accepted_the_text(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ("一",))
    store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="一",
        actor="human",
        evidence_refs=(),
    )

    summary = assemble_verified_content(store, project_id).acceptance

    assert summary.pairs == 1
    assert summary.human_accepted == 1
    assert summary.machine_accepted == 0
    assert not summary.requires_human_review
    assert summary.machine_acceptance == 0.0


def test_assembly_flags_machine_accepted_text_that_needs_a_spot_check(
    tmp_path: Path,
) -> None:
    """A low-confidence agreement is exportable, but the operator should know."""

    store, project_id = _store(tmp_path)
    store.add_page(project_id, 0, "pages/0.png", 100, 200)
    primary_id = store.record_ocr(
        project_id, PageOCRResult(0, "primary", "1.0", (_line("鸦", 0.1),))
    )
    secondary_id = store.record_ocr(
        project_id, PageOCRResult(0, "secondary", "1.0", (_line("鸦", 0.1),))
    )
    store.record_verification(
        primary_id,
        secondary_id,
        PageAlignment(0, "primary", "secondary", (LineAlignment(0, 0, 1.0, 0.0, ()),)),
        (PairRisk(0, 0.25, (RiskReason.LOW_CONFIDENCE,), min_confidence=0.1, content_score=0.25),),
        (),
    )
    store.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="鸦",
        actor="automatic",
        evidence_refs=(),
    )

    summary = assemble_verified_content(store, project_id).acceptance

    assert summary.machine_accepted == 1
    assert summary.requires_human_review
    assert summary.spot_check[0].note == ""
    assert summary.spot_check[0].score == 0.25


def test_assembly_returns_an_empty_book_without_pages(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    book = assemble_verified_content(store, project_id)

    assert book.pages == ()


def test_assembly_blocks_on_unresolved_pair(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ("一", "二"), undecided=frozenset({1}))

    with pytest.raises(UnresolvedContentError) as excinfo:
        assemble_verified_content(store, project_id)

    assert excinfo.value.unresolved_pairs == (UnresolvedPair(0, 1),)
    assert excinfo.value.unverified_pages == ()


def test_assembly_blocks_on_unverified_page(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ("一",))
    store.add_page(project_id, 1, "pages/1.png", 100, 200)

    with pytest.raises(UnresolvedContentError) as excinfo:
        assemble_verified_content(store, project_id)

    assert excinfo.value.unverified_pages == (1,)
    assert excinfo.value.unresolved_pairs == ()


def test_assembly_rejects_an_unknown_project(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    with pytest.raises(KeyError):
        assemble_verified_content(store, 99)
