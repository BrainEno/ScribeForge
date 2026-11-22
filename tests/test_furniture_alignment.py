"""Regressions for furniture evidence from different OCR runs and pair shapes.

These fixtures simulate OCR geometry; they are not real-model measurements.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from scribeforge.adapters.epub import build_epub
from scribeforge.adapters.export_check import check_epub
from scribeforge.cli import run
from scribeforge.domain.alignment import LineAlignment, PageAlignment
from scribeforge.domain.furniture import REASON_REPEATED_IN_MARGIN, detect_page_furniture
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.risk import PairRisk
from scribeforge.pipeline.adjudication import review_detail, review_queue
from scribeforge.pipeline.assembly import assemble_verified_content
from scribeforge.pipeline.furniture import PageFurniture, pair_furniture_reason, project_furniture
from scribeforge.pipeline.rounds import open_pairs, progress
from scribeforge.storage.sqlite import SQLiteStore


def line(text: str, *, header: bool = False) -> OCRLine:
    return OCRLine(
        text, BoundingBox(0.88, 0.04, 0.02, 0.01) if header else BoundingBox(0.12, 0.2, 0.75, 0.03)
    )


def test_furniture_indexes_belong_to_separate_engine_runs() -> None:
    page = PageFurniture({0: "primary-head"}, {2: "secondary-head"})
    assert pair_furniture_reason(page, LineAlignment(0, None, 0, None, ())) == "primary-head"
    assert pair_furniture_reason(page, LineAlignment(None, 2, 0, None, ())) == "secondary-head"
    assert pair_furniture_reason(page, LineAlignment(None, 0, 0, None, ())) is None
    assert pair_furniture_reason(page, LineAlignment(1, 2, 0, None, ())) is None
    assert pair_furniture_reason(page, LineAlignment(0, 2, 1, 0, ())) == "primary-head"


def test_multiline_pair_is_not_deleted_when_any_member_is_body() -> None:
    page = PageFurniture({0: "head"}, {0: "head"})
    mixed_primary = LineAlignment(0, 0, 0.9, 0, (), primary_line_indexes=(0, 1))
    mixed_secondary = LineAlignment(0, 0, 0.9, 0, (), secondary_line_indexes=(0, 1))
    assert pair_furniture_reason(page, mixed_primary) is None
    assert pair_furniture_reason(page, mixed_secondary) is None


def test_margin_keys_ignore_leading_and_trailing_space_not_internal_space() -> None:
    pages = {
        index: PageOCRResult(index, "primary", "fixture", (line(header, header=True), line("正文")))
        for index, header in enumerate((" V.", "V. ", "V."))
    }
    assert all(
        detected[0] == REASON_REPEATED_IN_MARGIN
        for detected in detect_page_furniture(pages).values()
    )
    altered = {
        index: PageOCRResult(index, "primary", "fixture", (line(header, header=True), line("正文")))
        for index, header in enumerate(("V. A", "V.A", "V. A"))
    }
    assert detect_page_furniture(altered) == {}


def _store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    return store, store.create_project("Book", "book.pdf", "hash")


def _secondary_header_page(store: SQLiteStore, pid: int, index: int, *, decide_header=False) -> int:
    store.add_page(pid, index, f"{index}.png", 100, 100)
    primary = PageOCRResult(index, "primary", "fixture", (line("含 V 的正文。"),))
    secondary = PageOCRResult(
        index, "secondary", "fixture", (line("V.", header=True), line("含 V 的正文。"))
    )
    a = store.record_ocr(pid, primary)
    b = store.record_ocr(pid, secondary)
    pairs = (LineAlignment(None, 0, 0, None, ()), LineAlignment(0, 1, 1, 0, ()))
    verification = store.record_verification(
        a,
        b,
        PageAlignment(index, "primary", "secondary", pairs),
        (PairRisk(0, 0.5, ()), PairRisk(1, 0, ())),
        (),
    )
    store.append_decision(
        project_id=pid,
        page_index=index,
        pair_index=1,
        selected_text="含 V 的正文。",
        actor="human",
        evidence_refs=(),
    )
    if decide_header:
        store.append_decision(
            project_id=pid,
            page_index=index,
            pair_index=0,
            selected_text="V.",
            actor="human",
            evidence_refs=(),
        )
    return verification


def test_secondary_only_header_is_filtered_from_verified_epub_and_auditable(
    tmp_path, capsys
) -> None:
    store, pid = _store(tmp_path)
    for index in range(3):
        _secondary_header_page(store, pid, index, decide_header=True)
    expected = project_furniture(store, pid)
    assert all(
        page.secondary == {0: REASON_REPEATED_IN_MARGIN} and not page.primary
        for page in expected.values()
    )
    book = assemble_verified_content(store, pid)
    assert [page.lines for page in book.pages] == [("含 V 的正文。",)] * 3
    assert [item.text for item in book.dropped_furniture] == ["V."] * 3
    assert [
        (item.pair_index, item.primary_line_indexes, item.secondary_line_indexes)
        for item in book.dropped_furniture
    ] == [(0, (), (0,))] * 3
    restored = assemble_verified_content(store, pid, include_page_furniture=True)
    assert [page.lines for page in restored.pages] == [("V.", "含 V 的正文。")] * 3
    epub = build_epub(
        book,
        identifier="urn:test:fixture",
        language="zh",
        modified=datetime(2024, 1, 1, tzinfo=UTC),
        layout="page",
    )
    check_epub(epub, book, layout="page")
    with zipfile.ZipFile(io.BytesIO(epub)) as archive:
        text = archive.read("OEBPS/page-000001.xhtml").decode()
    assert "含 V 的正文。" in text and ">V.</" not in text
    destination = tmp_path / "book.epub"
    assert run(["export", "--store", str(tmp_path), "--out", str(destination), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dropped_furniture"] == 3
    assert [
        (entry["page_index"], entry["pair_index"], entry["secondary_line_indexes"])
        for entry in report["dropped_furniture_details"]
    ] == [(i, 0, [0]) for i in range(3)]


def test_secondary_only_header_removed_consistently_from_queue_and_progress(tmp_path) -> None:
    store, pid = _store(tmp_path)
    for index in range(3):
        _secondary_header_page(store, pid, index)
    assert review_queue(store, pid) == ()
    assert open_pairs(store, pid) == ()
    assert len(review_queue(store, pid, include_page_furniture=True)) == 3
    assert len(open_pairs(store, pid, include_page_furniture=True)) == 3
    assert progress(store, pid).pairs == 3
    assert progress(store, pid, include_page_furniture=True).pairs == 6
    assert review_detail(store, pid, 0, 0).furniture_reason == REASON_REPEATED_IN_MARGIN
    assert [page.lines for page in assemble_verified_content(store, pid).pages] == [
        ("含 V 的正文。",)
    ] * 3


def test_latest_unverified_run_cannot_replace_verification_furniture(tmp_path) -> None:
    store, pid = _store(tmp_path)
    for index in range(3):
        _secondary_header_page(store, pid, index)
        store.record_ocr(pid, PageOCRResult(index, "secondary", "later", (line("正文"),)))
        store.record_ocr(
            pid,
            PageOCRResult(index, "primary", "crop", (line("V.", header=True), line("正文"))),
            scope="pair:0",
        )
    assert len(project_furniture(store, pid)) == 3
    assert len(assemble_verified_content(store, pid).dropped_furniture) == 3


def test_human_replacement_different_from_both_header_readings_is_not_discarded(tmp_path) -> None:
    store, pid = _store(tmp_path)
    for index in range(3):
        _secondary_header_page(store, pid, index)
        store.append_decision(
            project_id=pid,
            page_index=index,
            pair_index=0,
            selected_text="人工补入正文",
            actor="human",
            evidence_refs=(),
        )
    book = assemble_verified_content(store, pid)
    assert [page.lines for page in book.pages] == [("人工补入正文", "含 V 的正文。")] * 3
    assert book.dropped_furniture == ()
    assert progress(store, pid).pairs == 6


def test_sparse_single_line_pages_do_not_supply_repetition_evidence() -> None:
    pages = {
        0: PageOCRResult(0, "primary", "1", (line("V.", header=True),)),
        1: PageOCRResult(1, "primary", "1", (line("V.", header=True),)),
        2: PageOCRResult(2, "primary", "1", (line("V.", header=True), line("正文"))),
    }
    assert detect_page_furniture(pages) == {}


def test_ambiguous_alignment_is_never_omitted_even_if_all_members_look_like_headers() -> None:
    from scribeforge.domain.alignment import (
        AlignmentAlternative,
        AlignmentAmbiguity,
        AlignmentStatus,
        AmbiguityReason,
    )

    alternative1 = AlignmentAlternative((0,), (0,), 1, 1, 0)
    alternative2 = AlignmentAlternative((1,), (0,), 0.9, 1, 0)
    pair = LineAlignment(
        0,
        0,
        1,
        0,
        (),
        primary_line_indexes=(0, 1),
        status=AlignmentStatus.AMBIGUOUS,
        ambiguity=AlignmentAmbiguity(
            AmbiguityReason.CLOSE_SCORES, 0.1, (alternative1, alternative2)
        ),
    )
    assert pair_furniture_reason(PageFurniture({0: "head", 1: "head"}, {0: "head"}), pair) is None


def test_mixed_group_stays_in_review_and_export_keeps_human_decision(tmp_path) -> None:
    import pytest

    from scribeforge.pipeline.assembly import UnresolvedContentError

    store, pid = _store(tmp_path)
    for index in range(3):
        store.add_page(pid, index, f"{index}.png", 100, 100)
        lines = (line("V.", header=True), line("正文。"))
        a = store.record_ocr(pid, PageOCRResult(index, "primary", "1", lines))
        b = store.record_ocr(pid, PageOCRResult(index, "secondary", "1", lines))
        pair = LineAlignment(
            0, 0, 1, 0, (), primary_line_indexes=(0, 1), secondary_line_indexes=(0, 1)
        )
        store.record_verification(
            a, b, PageAlignment(index, "primary", "secondary", (pair,)), (PairRisk(0, 0, ()),), ()
        )
    assert len(review_queue(store, pid)) == 3
    assert len(open_pairs(store, pid)) == 3
    with pytest.raises(UnresolvedContentError):
        assemble_verified_content(store, pid)
    for index in range(3):
        store.append_decision(
            project_id=pid,
            page_index=index,
            pair_index=0,
            selected_text="正文。",
            actor="human",
            evidence_refs=(),
        )
    book = assemble_verified_content(store, pid)
    assert [p.lines for p in book.pages] == [("正文。",)] * 3
    assert book.dropped_furniture == ()


def test_repeated_body_text_and_unverified_pages_do_not_become_header_evidence(tmp_path) -> None:
    store, pid = _store(tmp_path)
    for index in range(3):
        store.add_page(pid, index, f"{index}.png", 100, 100)
        body = PageOCRResult(index, "primary", "fixture", (line("V."), line("正文")))
        a = store.record_ocr(pid, body)
        b = store.record_ocr(pid, PageOCRResult(index, "secondary", "fixture", body.lines))
        store.record_verification(
            a,
            b,
            PageAlignment(
                index,
                "primary",
                "secondary",
                (LineAlignment(0, 0, 1, 0, ()), LineAlignment(1, 1, 1, 0, ())),
            ),
            (PairRisk(0, 0, ()), PairRisk(1, 0, ())),
            (),
        )
        store.append_decision(
            project_id=pid,
            page_index=index,
            pair_index=0,
            selected_text="V.",
            actor="human",
            evidence_refs=(),
        )
        store.append_decision(
            project_id=pid,
            page_index=index,
            pair_index=1,
            selected_text="正文",
            actor="human",
            evidence_refs=(),
        )
    for index in range(3, 6):
        store.add_page(pid, index, f"{index}.png", 100, 100)
        store.record_ocr(
            pid, PageOCRResult(index, "primary", "fixture", (line("V.", header=True), line("正文")))
        )
    book = None
    # Missing verification blocks export instead of accepting a partial book.
    import pytest

    from scribeforge.pipeline.assembly import UnresolvedContentError

    with pytest.raises(UnresolvedContentError):
        book = assemble_verified_content(store, pid)
    assert book is None
    assert project_furniture(store, pid) == {}
    assert progress(store, pid).pairs == 6
