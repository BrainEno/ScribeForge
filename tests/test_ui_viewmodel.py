from __future__ import annotations

from pathlib import Path

from scribeforge.domain.alignment import (
    AlignmentConflict,
    ConflictKind,
    LineAlignment,
    PageAlignment,
)
from scribeforge.domain.crops import review_crop_file_name
from scribeforge.domain.jobs import JobStage
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import PairRisk, ReviewCandidate, RiskReason
from scribeforge.pipeline.adjudication import ReviewDetail, record_human_decision, review_detail
from scribeforge.storage.sqlite import SQLiteStore
from scribeforge.ui.viewmodel import (
    load_detail,
    load_jobs,
    load_library,
    load_pair_view,
    load_review_rows,
    page_image_path,
    render_detail,
    resolve_review_row,
)


def _line(text: str, y: float) -> OCRLine:
    box = BoundingBox(0.1, y, 0.5, 0.05)
    return OCRLine(text, box, (OCRToken(text, 0.9, box),))


def _store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, str(tmp_path / "pages" / "0.png"), 100, 200)

    primary_id = store.record_ocr(
        project_id,
        PageOCRResult(0, "primary", "1.0", (_line("鸦", 0.1), _line("乙", 0.5))),
    )
    secondary_id = store.record_ocr(
        project_id,
        PageOCRResult(0, "secondary", "1.0", (_line("雅", 0.12), _line("乙", 0.5))),
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
    risks = (PairRisk(0, 0.5, (RiskReason.TEXT_DISAGREEMENT,)), PairRisk(1, 0.0, ()))
    candidates = (
        ReviewCandidate(
            0,
            0,
            0.5,
            (RiskReason.TEXT_DISAGREEMENT,),
            BoundingBox(0.1, 0.1, 0.5, 0.05),
        ),
    )
    store.record_verification(primary_id, secondary_id, alignment, risks, candidates)
    return store, project_id


def _empty_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    return store


def test_load_library_is_empty_without_projects(tmp_path: Path) -> None:
    assert load_library(_empty_store(tmp_path)) == ()


def test_load_library_summarizes_each_project(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    import_job = store.ensure_job(project_id, JobStage.IMPORT)
    store.start_job(import_job)
    store.complete_job(import_job)
    ocr_job = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    store.start_job(ocr_job)
    store.fail_job(ocr_job, "engine crashed")

    entries = load_library(store)

    assert len(entries) == 1
    entry = entries[0]
    assert (entry.project_id, entry.title, entry.source_uri) == (project_id, "Book", "book.pdf")
    assert (entry.page_count, entry.review_queued, entry.failed_jobs) == (1, 1, 1)
    assert entry.summary == "Book — 1 page(s), 1 queued for review, 1 failed job(s)"


def test_load_jobs_labels_the_scope_and_the_failure(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    import_job = store.ensure_job(project_id, JobStage.IMPORT)
    store.start_job(import_job)
    store.complete_job(import_job)
    ocr_job = store.ensure_job(project_id, JobStage.PRIMARY_OCR, page_index=0)
    store.start_job(ocr_job)
    store.fail_job(ocr_job, "engine crashed")

    rows = load_jobs(store, project_id)

    assert [row.label for row in rows] == [
        "import [project] succeeded attempts=1",
        "primary_ocr [page 0] failed attempts=1 error=engine crashed",
    ]


def test_load_review_rows_lists_the_queued_pairs(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    rows = load_review_rows(store, project_id)

    assert len(rows) == 1
    assert rows[0].label == "page 0 pair 0 score=0.50 reasons=text_disagreement"


def test_load_review_rows_is_empty_once_a_human_decided(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    record_human_decision(store, project_id, 0, 0, "鸦")

    assert load_review_rows(store, project_id) == ()


def test_load_detail_reads_the_queued_pair(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    row = load_review_rows(store, project_id)[0]

    detail = load_detail(store, project_id, row)

    assert (detail.page_index, detail.pair_index) == (0, 0)
    assert (detail.primary_text, detail.secondary_text) == ("鸦", "雅")
    assert detail.vlm_text is None
    assert detail.accepted_text is None


def test_load_detail_exposes_source_and_engine_line_boxes(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    row = load_review_rows(store, project_id)[0]

    detail = load_detail(store, project_id, row)

    assert detail.source_box == BoundingBox(0.1, 0.1, 0.5, 0.05)
    assert detail.primary_box == BoundingBox(0.1, 0.1, 0.5, 0.05)
    assert detail.secondary_box == BoundingBox(0.1, 0.12, 0.5, 0.05)


def test_page_image_path_is_none_when_the_asset_is_gone(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    assert page_image_path(store, project_id, 0) is None


def test_page_image_path_resolves_the_imported_asset(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    asset = tmp_path / "pages" / "0.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"page")

    assert page_image_path(store, project_id, 0) == asset


def test_page_image_path_is_none_for_an_unknown_page(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)

    assert page_image_path(store, project_id, 9) is None


def test_load_pair_view_reports_the_missing_images(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    row = load_review_rows(store, project_id)[0]

    view = load_pair_view(store, project_id, row)

    assert view.page_image is None
    assert view.crop_image is None
    assert view.sources_label == "no page image, no crop"
    assert (view.detail.page_index, view.detail.pair_index) == (0, 0)


def test_load_pair_view_reports_the_page_asset_and_the_crop(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    asset = tmp_path / "pages" / "0.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"page")
    crops_dir = tmp_path / "crops"
    crops_dir.mkdir()
    crop = crops_dir / review_crop_file_name(0, 0)
    crop.write_bytes(b"crop")
    row = load_review_rows(store, project_id)[0]

    view = load_pair_view(store, project_id, row, crops_dir=crops_dir)

    assert view.page_image == asset
    assert view.crop_image == crop
    assert view.sources_label == "page image, crop"


def test_render_detail_lays_out_every_evidence_field() -> None:
    detail = ReviewDetail(
        page_index=0,
        pair_index=0,
        score=0.5,
        reasons=(RiskReason.TEXT_DISAGREEMENT,),
        primary_text="鸦",
        secondary_text="雅",
        vlm_text=None,
        vlm_uncertain=False,
        crop_path=None,
        accepted_text=None,
    )

    assert render_detail(detail).splitlines() == [
        "page 0 pair 0",
        "risk score=0.50 reasons=text_disagreement",
        "primary '鸦'",
        "secondary '雅'",
        "vlm none",
        "crop none",
        "accepted none",
    ]


def test_render_detail_marks_a_vlm_reading_and_a_crop() -> None:
    detail = ReviewDetail(
        page_index=3,
        pair_index=1,
        score=0.25,
        reasons=(RiskReason.SPATIAL_MISMATCH, RiskReason.LOW_CONFIDENCE),
        primary_text=None,
        secondary_text="",
        vlm_text="ok",
        vlm_uncertain=True,
        crop_path="/store/crops/page-000004-pair-000002.png",
        accepted_text="ok",
    )

    assert render_detail(detail).splitlines() == [
        "page 3 pair 1",
        "risk score=0.25 reasons=spatial_mismatch,low_confidence",
        "primary none",
        "secondary ''",
        "vlm 'ok' uncertain=true",
        "crop /store/crops/page-000004-pair-000002.png",
        "accepted 'ok'",
    ]


def test_render_detail_shows_an_accepted_human_reading(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    record_human_decision(store, project_id, 0, 0, "鸦")

    text = render_detail(review_detail(store, project_id, 0, 0))

    assert "accepted '鸦'" in text.splitlines()



def test_resolve_review_row_records_the_literal_decision_and_removes_queue_item(
    tmp_path: Path,
) -> None:
    store, project_id = _store(tmp_path)
    row = load_review_rows(store, project_id)[0]

    decision_id = resolve_review_row(store, project_id, row, "  原样校对文本  ")

    decision = store.latest_decision(project_id, row.page_index, row.pair_index)
    assert decision_id > 0
    assert decision is not None
    assert decision.selected_text == "  原样校对文本  "
    assert load_review_rows(store, project_id) == ()


def test_resolve_review_row_rejects_a_stale_queue_item(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    row = load_review_rows(store, project_id)[0]
    resolve_review_row(store, project_id, row, "accepted")

    import pytest

    with pytest.raises(ValueError, match="no longer queued"):
        resolve_review_row(store, project_id, row, "different")
