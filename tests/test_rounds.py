"""Repair rounds: re-read a disputed line, accept only what readers agree on."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.pipeline.orchestrator import process_page
from scribeforge.pipeline.rounds import (
    RepairReader,
    RepairSettings,
    open_pairs,
    progress,
    region_scope,
    repair_book,
    run_repair_round,
)
from scribeforge.storage.sqlite import SQLiteStore


class SplitEnglishEngine:
    """Deterministic engine returning one logical English line as two OCR lines."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        return PageOCRResult(
            page_index,
            self._name,
            "1.0",
            (
                OCRLine("The quick", BoundingBox(0.1, 0.1, 0.8, 0.02)),
                OCRLine("brown fox", BoundingBox(0.1, 0.12, 0.8, 0.02)),
            ),
        )


class FakeEngine:
    """Deterministic engine whose answer can change between calls."""

    def __init__(self, name: str, *texts: str) -> None:
        self._name = name
        self._texts = list(texts)
        self.calls = 0
        self.last_image = ""
        self.seen_bytes: list[bytes] = []

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        self.calls += 1
        self.last_image = image_path
        self.seen_bytes.append(Path(image_path).read_bytes())
        text = self._texts[min(self.calls - 1, len(self._texts) - 1)]
        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        return PageOCRResult(page_index, self._name, "1.0", (OCRLine(text, box),))


def _disputed_page(tmp_path: Path) -> tuple[SQLiteStore, int]:
    """A processed page whose single pair is open: two engines disagree."""

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    image_path = tmp_path / "page-0.png"
    Image.new("RGB", (100, 150), "white").save(image_path)
    store.add_page(project_id, 0, str(image_path), 100, 150)
    process_page(
        store,
        project_id,
        store.list_pages(project_id)[0],
        primary_engine=FakeEngine("mineru", "鸦"),
        secondary_engine=FakeEngine("paddleocr", "雅"),
        crops_dir=tmp_path / "crops",
    )
    return store, project_id


def test_crop_rereads_use_the_same_english_group_text_as_acceptance(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    image_path = tmp_path / "page-0.png"
    Image.new("RGB", (100, 150), "white").save(image_path)
    store.add_page(project_id, 0, str(image_path), 100, 150)
    process_page(
        store,
        project_id,
        store.list_pages(project_id)[0],
        primary_engine=SplitEnglishEngine("mineru"),
        secondary_engine=FakeEngine("paddleocr", "The quick brown cat"),
        crops_dir=tmp_path / "crops",
    )
    assert len(open_pairs(store, project_id)) == 1

    report = run_repair_round(
        store,
        project_id,
        readers=(
            RepairReader("first-crop", SplitEnglishEngine("first-crop")),
            RepairReader("second-crop", SplitEnglishEngine("second-crop")),
        ),
        crops_dir=tmp_path / "repair",
    )

    assert report.progressed
    decision = store.latest_decision(project_id, 0, 0)
    assert decision is not None
    assert decision.selected_text == "The quick brown fox"
    assert decision.actor == "automatic"


def test_a_round_settles_a_pair_when_re_reads_corroborate_one_engine(
    tmp_path: Path,
) -> None:
    store, project_id = _disputed_page(tmp_path)
    assert len(open_pairs(store, project_id)) == 1

    report = run_repair_round(
        store,
        project_id,
        readers=(
            RepairReader("mineru-crop", FakeEngine("mineru", "鸦")),
            RepairReader("paddleocr-crop", FakeEngine("paddleocr", "鸦")),
        ),
        crops_dir=tmp_path / "repair",
    )

    assert report.examined and report.progressed
    decision = store.latest_decision(project_id, 0, 0)
    assert decision is not None
    assert decision.selected_text == "鸦"
    assert decision.actor == "automatic"
    # The decision cites the fresh reads, not just the original disagreement.
    assert f"region_read:{region_scope(0)}" in decision.evidence_refs
    assert "consensus:majority" in decision.evidence_refs
    assert open_pairs(store, project_id) == ()


def test_a_round_leaves_the_pair_open_when_the_readers_disagree(
    tmp_path: Path,
) -> None:
    store, project_id = _disputed_page(tmp_path)

    report = run_repair_round(
        store,
        project_id,
        readers=(
            RepairReader("one", FakeEngine("mineru", "鸡")),
            RepairReader("two", FakeEngine("paddleocr", "蛙")),
        ),
        crops_dir=tmp_path / "repair",
    )

    assert report.examined == report.still_open
    assert not report.progressed
    assert store.latest_decision(project_id, 0, 0) is None


def test_the_second_attempt_reads_a_wider_larger_crop(tmp_path: Path) -> None:
    """Repeating an identical crop could only reproduce the identical text."""

    store, project_id = _disputed_page(tmp_path)
    reader = FakeEngine("mineru", "鸡")

    first = run_repair_round(
        store,
        project_id,
        readers=(RepairReader("crop", reader),),
        crops_dir=tmp_path / "repair",
        round_index=1,
    )
    first_crop = Path(reader.last_image)
    second = run_repair_round(
        store,
        project_id,
        readers=(RepairReader("crop", reader),),
        crops_dir=tmp_path / "repair",
        round_index=2,
    )
    second_crop = Path(reader.last_image)

    assert not first.progressed and not second.progressed
    assert first_crop.name.endswith("-read-01.png")
    assert second_crop.name.endswith("-read-02.png")
    assert not first_crop.exists() and not second_crop.exists()
    assert reader.seen_bytes[0] == (tmp_path / "repair" / first_crop.name).read_bytes()
    assert reader.seen_bytes[1] == (tmp_path / "repair" / second_crop.name).read_bytes()
    with (
        Image.open(BytesIO(reader.seen_bytes[0])) as before,
        Image.open(BytesIO(reader.seen_bytes[1])) as after,
    ):
        assert after.size[0] > before.size[0]
        assert after.size[1] >= before.size[1]


def test_attempts_are_bounded_so_an_illegible_pair_reaches_a_person(
    tmp_path: Path,
) -> None:
    """Escalation is finite: after the budget the pair belongs to a human."""

    store, project_id = _disputed_page(tmp_path)
    reader = FakeEngine("mineru", "鸡")
    settings = RepairSettings(max_rounds=5, max_attempts_per_pair=2)

    reports = repair_book(
        store,
        project_id,
        readers=(RepairReader("crop", reader),),
        crops_dir=tmp_path / "repair",
        settings=settings,
    )

    assert reader.calls == 2
    assert reports[-1].skipped
    assert store.latest_decision(project_id, 0, 0) is None
    assert progress(store, project_id).needs_review


def test_rounds_keep_escalating_while_attempts_remain(tmp_path: Path) -> None:
    """A failed attempt changes the crop, so another one is worth trying."""

    store, project_id = _disputed_page(tmp_path)
    reader = FakeEngine("mineru", "鸡")

    reports = repair_book(
        store,
        project_id,
        readers=(RepairReader("crop", reader),),
        crops_dir=tmp_path / "repair",
        settings=RepairSettings(max_rounds=4, max_attempts_per_pair=3),
    )

    assert reader.calls == 3
    assert sum(len(report.examined) for report in reports) == 3
    # The last report is the one that says the pair is out of attempts.
    assert reports[-1].examined == ()
    assert reports[-1].skipped


def test_rounds_stop_once_every_pair_is_settled(tmp_path: Path) -> None:
    store, project_id = _disputed_page(tmp_path)

    reports = repair_book(
        store,
        project_id,
        readers=(RepairReader("crop", FakeEngine("paddleocr", "鸦")),),
        crops_dir=tmp_path / "repair",
        settings=RepairSettings(max_rounds=4),
    )

    assert len(reports) == 1
    assert reports[0].progressed
    assert open_pairs(store, project_id) == ()


class Reviewer:
    """A vision model that answers something for every crop."""

    def __init__(self, text: str, *, uncertain: bool = False) -> None:
        self._text = text
        self._uncertain = uncertain

    def transcribe_crop(self, image_path: str, page_index: int, pair_index: int):
        from scribeforge.domain.vlm import VLMReading

        return VLMReading(
            page_index,
            pair_index,
            "fake-vlm",
            "1.0",
            self._text,
            self._uncertain,
        )


def test_a_round_re_asks_the_vision_model_on_the_bigger_crop(tmp_path: Path) -> None:
    """An uncertain first answer is not a dead end: the crop, not the model, changed."""

    store = SQLiteStore(tmp_path / "with-vlm" / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    image_path = tmp_path / "with-vlm" / "page-0.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 150), "white").save(image_path)
    store.add_page(project_id, 0, str(image_path), 100, 150)
    process_page(
        store,
        project_id,
        store.list_pages(project_id)[0],
        primary_engine=FakeEngine("mineru", "鸦"),
        secondary_engine=FakeEngine("paddleocr", "雅"),
        reviewer=Reviewer("鸦", uncertain=True),
        crops_dir=tmp_path / "crops-vlm",
    )
    assert len(open_pairs(store, project_id)) == 1

    report = run_repair_round(
        store,
        project_id,
        readers=(RepairReader("crop", FakeEngine("mineru", "鸡")),),
        reviewer=Reviewer("鸦"),
        crops_dir=tmp_path / "repair",
    )

    assert report.progressed
    decision = store.latest_decision(project_id, 0, 0)
    assert decision is not None
    assert decision.selected_text == "鸦"
    # The vision model supplied the second vote, so the decision is recorded as
    # such — not as a silent automatic accept.
    assert decision.actor == "vlm"
    assert any(
        ref.startswith("vlm_review:") and ref != "vlm_review:unknown"
        for ref in decision.evidence_refs
    )


def test_progress_reports_what_still_needs_a_person(tmp_path: Path) -> None:
    store, project_id = _disputed_page(tmp_path)

    before = progress(store, project_id)
    assert before.pages == 1
    assert before.pairs == 1
    assert before.open_pairs == 1
    assert before.needs_review
    assert before.auto_acceptance == 0.0

    run_repair_round(
        store,
        project_id,
        readers=(RepairReader("crop", FakeEngine("paddleocr", "鸦")),),
        crops_dir=tmp_path / "repair",
    )

    after = progress(store, project_id)
    assert after.open_pairs == 0
    assert after.accepted_automatic == 1
    assert not after.needs_review
    assert after.auto_acceptance == 1.0


def test_a_round_needs_a_reader(tmp_path: Path) -> None:
    store, project_id = _disputed_page(tmp_path)

    with pytest.raises(ValueError, match="reader"):
        run_repair_round(store, project_id, readers=(), crops_dir=tmp_path / "repair")


def test_reserved_reader_labels_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="reserved"):
        RepairReader("vlm", FakeEngine("mineru", "鸦"))


def test_settings_validate_and_escalate() -> None:
    settings = RepairSettings(base_padding=0.01, padding_step=0.02, max_padding=0.04)

    assert settings.escalation(0) == (0.01, 1)
    assert settings.escalation(1) == (0.03, 2)
    # Padding and scale both stop at their configured ceilings.
    assert settings.escalation(2) == (0.04, 3)
    assert settings.escalation(99) == (0.04, 3)

    with pytest.raises(ValueError, match="max_rounds"):
        RepairSettings(max_rounds=0)
    with pytest.raises(ValueError, match="max_scale"):
        RepairSettings(max_scale=0)


class ConfidentTextEngine:
    """A reader that reports the confidence of its own reading."""

    def __init__(self, name: str, text: str, confidence: float) -> None:
        self._name = name
        self._text = text
        self._confidence = confidence

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        from scribeforge.domain.ocr import OCRToken

        box = BoundingBox(0.1, 0.1, 0.8, 0.02)
        token = OCRToken(self._text, self._confidence, box)
        return PageOCRResult(page_index, self._name, "1.0", (OCRLine(self._text, box, (token,)),))


def _page_with(
    tmp_path: Path, primary: ConfidentTextEngine, secondary: ConfidentTextEngine
) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    image_path = tmp_path / "page-0.png"
    Image.new("RGB", (100, 150), "white").save(image_path)
    store.add_page(project_id, 0, str(image_path), 100, 150)
    process_page(
        store,
        project_id,
        store.list_pages(project_id)[0],
        primary_engine=primary,
        secondary_engine=secondary,
        crops_dir=tmp_path / "crops",
    )
    return store, project_id


def test_progress_separates_structural_disagreement_from_blocked_text(
    tmp_path: Path,
) -> None:
    """A plain disagreement is a question for a reader; bad text is not open at all.

    The two are different kinds of work — one needs a decision, the other needs a
    better reading — so the summary must not lump them together.
    """

    store, project_id = _disputed_page(tmp_path)
    assert progress(store, project_id).open_blocked == 0

    blocked_store, blocked_project = _page_with(
        tmp_path / "blocked",
        ConfidentTextEngine("mineru", "\ufffd", 0.1),
        ConfidentTextEngine("paddleocr", "\ufffd", 0.1),
    )
    report = progress(blocked_store, blocked_project)

    assert report.open_pairs == 1
    assert report.open_blocked == 1


@pytest.mark.parametrize("text", ["\ufffd", "bad\x0btext"])
def test_repair_cannot_accept_unrepresentable_consensus(tmp_path: Path, text: str) -> None:
    store, project_id = _page_with(
        tmp_path,
        ConfidentTextEngine("mineru", text, 0.9),
        ConfidentTextEngine("paddleocr", text, 0.9),
    )
    assert open_pairs(store, project_id)
    report = run_repair_round(
        store,
        project_id,
        readers=(
            RepairReader("one", FakeEngine("mineru", text)),
            RepairReader("two", FakeEngine("paddleocr", text)),
        ),
        crops_dir=tmp_path / "repair",
    )
    assert report.still_open
    assert store.latest_decision(project_id, 0, 0) is None
    reopened = SQLiteStore(tmp_path / "project.sqlite3")
    reopened.initialize()
    assert open_pairs(reopened, project_id)


def test_repair_cannot_bypass_low_confidence_even_with_independent_agreement(
    tmp_path: Path,
) -> None:
    store, project_id = _page_with(
        tmp_path,
        ConfidentTextEngine("mineru", "鸦", 0.1),
        ConfidentTextEngine("paddleocr", "鸦", 0.1),
    )
    report = run_repair_round(
        store,
        project_id,
        readers=(RepairReader("third", FakeEngine("third", "鸦")),),
        crops_dir=tmp_path / "repair",
    )
    assert report.still_open
    assert store.latest_decision(project_id, 0, 0) is None


def test_repair_does_not_count_repeated_engine_as_independent(tmp_path: Path) -> None:
    store, project_id = _disputed_page(tmp_path)
    report = run_repair_round(
        store,
        project_id,
        readers=(
            RepairReader("one", FakeEngine("mineru", "鸦")),
            RepairReader("two", FakeEngine("paddleocr", "蛙")),
        ),
        crops_dir=tmp_path / "repair",
    )
    assert report.still_open
    assert store.latest_decision(project_id, 0, 0) is None


def test_repair_records_original_risk_with_independent_corroboration(tmp_path: Path) -> None:
    store, project_id = _disputed_page(tmp_path)
    verification = store.load_verification(store.latest_verification_id(project_id, 0))
    run_repair_round(
        store,
        project_id,
        readers=(
            RepairReader("one", FakeEngine("mineru", "鸦")),
            RepairReader("two", FakeEngine("paddleocr", "鸦")),
        ),
        crops_dir=tmp_path / "repair",
    )
    decision = store.latest_decision(project_id, 0, 0)
    assert decision is not None
    assert decision.risk_score == verification.risks[0].score
    assert decision.risk_reasons == tuple(r.value for r in verification.risks[0].reasons)
    assert len([ref for ref in decision.evidence_refs if ref.startswith("engine_run:")]) >= 3


def test_repair_respects_persisted_spatial_mismatch(tmp_path: Path) -> None:
    import sqlite3

    store, project_id = _disputed_page(tmp_path)
    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute(
            "UPDATE risks SET reasons_json = ?, content_score = ?",
            ('["spatial_mismatch"]', 0.2),
        )
    report = run_repair_round(
        store,
        project_id,
        readers=(RepairReader("second", FakeEngine("paddleocr", "鸦")),),
        crops_dir=tmp_path / "repair",
    )
    assert report.still_open
    assert store.latest_decision(project_id, 0, 0) is None


def test_repair_does_not_accept_low_confidence_crop_corroboration(tmp_path: Path) -> None:
    store, project_id = _disputed_page(tmp_path)
    report = run_repair_round(
        store,
        project_id,
        readers=(RepairReader("second", ConfidentTextEngine("paddleocr", "鸦", 0.1)),),
        crops_dir=tmp_path / "repair",
    )
    assert report.still_open
    assert store.latest_decision(project_id, 0, 0) is None


def test_engine_version_does_not_create_an_independent_source(tmp_path: Path) -> None:
    from dataclasses import replace

    class NewVersionEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            return replace(super().analyze_page(image_path, page_index), engine_version="2.0")

    store, project_id = _disputed_page(tmp_path)
    report = run_repair_round(
        store,
        project_id,
        readers=(RepairReader("upgraded", NewVersionEngine("mineru", "鸦")),),
        crops_dir=tmp_path / "repair",
    )
    assert report.still_open
    assert store.latest_decision(project_id, 0, 0) is None


def test_repair_rejects_worker_identity_mismatch(tmp_path: Path) -> None:
    from dataclasses import replace

    class MismatchedEngine(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            return replace(super().analyze_page(image_path, page_index), engine="impostor")

    store, project_id = _disputed_page(tmp_path)
    with pytest.raises(ValueError, match="mismatched engine or page identity"):
        run_repair_round(
            store,
            project_id,
            readers=(RepairReader("wrong", MismatchedEngine("mineru", "鸦")),),
            crops_dir=tmp_path / "repair",
        )
    assert store.latest_decision(project_id, 0, 0) is None


def test_repair_refuses_replaced_original_page_before_reading(tmp_path: Path) -> None:
    from scribeforge.domain.page_asset import PageAssetError, PageAssetEvidence

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    image = tmp_path / "page.png"
    Image.new("RGB", (100, 150), "white").save(image)
    store.add_page(
        project_id,
        0,
        str(image),
        100,
        150,
        page_asset=PageAssetEvidence.from_bytes(image.read_bytes()),
    )
    process_page(
        store,
        project_id,
        store.list_pages(project_id)[0],
        primary_engine=FakeEngine("mineru", "鸦"),
        secondary_engine=FakeEngine("paddleocr", "雅"),
        crops_dir=tmp_path / "crops",
    )
    image.write_bytes(b"replacement")
    engine = FakeEngine("paddleocr", "鸦")
    with pytest.raises(PageAssetError, match="source image"):
        run_repair_round(
            store,
            project_id,
            readers=(RepairReader("reread", engine),),
            crops_dir=tmp_path / "repair",
        )
    assert engine.calls == 0
    assert store.latest_decision(project_id, 0, 0) is None


def test_repair_decision_refuses_page_change_at_write_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project = _disputed_page(tmp_path)
    page = store.list_pages(project)[0]
    append = store.append_decision

    def backfill_before_write(*args, **kwargs):
        store.rehash_page_asset(page.id)
        return append(*args, **kwargs)

    monkeypatch.setattr(store, "append_decision", backfill_before_write)
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        run_repair_round(
            store,
            project,
            readers=(
                RepairReader("one", FakeEngine("mineru", "鸦")),
                RepairReader("two", FakeEngine("paddleocr", "鸦")),
            ),
            crops_dir=tmp_path / "repair",
        )
    assert store.latest_decision(project, 0, 0) is None
    original_runs = store.list_scoped_run_ids(project, 0, "pair:0")
    assert len(original_runs) == 2
    monkeypatch.setattr(store, "append_decision", append)
    report = run_repair_round(
        store,
        project,
        readers=(
            RepairReader("one", FakeEngine("mineru", "鸦")),
            RepairReader("two", FakeEngine("paddleocr", "鸦")),
        ),
        crops_dir=tmp_path / "repair",
    )
    assert report.progressed
    assert store.latest_decision(project, 0, 0).selected_text == "鸦"
    assert store.list_scoped_run_ids(project, 0, "pair:0")[:2] == original_runs


def test_human_decision_refuses_page_change_at_write_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.pipeline.adjudication import record_human_decision

    store, project = _disputed_page(tmp_path)
    page = store.list_pages(project)[0]
    append = store.append_decision

    def backfill_before_write(*args, **kwargs):
        store.rehash_page_asset(page.id)
        return append(*args, **kwargs)

    monkeypatch.setattr(store, "append_decision", backfill_before_write)
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        record_human_decision(store, project, 0, 0, "鸦")
    assert store.latest_decision(project, 0, 0) is None
    monkeypatch.setattr(store, "append_decision", append)
    record_human_decision(store, project, 0, 0, "鸦")
    assert store.latest_decision(project, 0, 0).selected_text == "鸦"


def test_repair_does_not_supersede_concurrent_human_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.pipeline.adjudication import record_human_decision

    store, project = _disputed_page(tmp_path)
    other = SQLiteStore(tmp_path / "project.sqlite3")
    append = store.append_decision

    def concurrent_human(**kwargs):
        record_human_decision(other, project, 0, 0, "人定文本")
        return append(**kwargs)

    monkeypatch.setattr(store, "append_decision", concurrent_human)
    report = run_repair_round(
        store,
        project,
        readers=(
            RepairReader("one", FakeEngine("mineru", "鸦")),
            RepairReader("two", FakeEngine("paddleocr", "鸦")),
        ),
        crops_dir=tmp_path / "repair",
    )
    decisions = store.list_decisions(project, 0, 0)
    assert [(d.actor, d.selected_text) for d in decisions] == [("human", "人定文本")]
    assert not report.progressed
    assert not report.skipped and not report.resolved and not report.remaining
    assert [(ref.page_index, ref.pair_index) for ref in report.already_decided] == [(0, 0)]
    from scribeforge.cli import _rounds_json

    summary = _rounds_json((report,))[0]
    assert summary["already_decided"] == 1
    assert summary["skipped"] == 0
    assert len(store.list_scoped_run_ids(project, 0, "pair:0")) == 2


def test_repair_reports_concurrent_human_decision_even_without_consensus(tmp_path: Path) -> None:
    from scribeforge.pipeline.adjudication import record_human_decision

    store, project = _disputed_page(tmp_path)
    other = SQLiteStore(tmp_path / "project.sqlite3")

    class HumanDuringRead(FakeEngine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            record_human_decision(other, project, 0, 0, "人工确认")
            return super().analyze_page(image_path, page_index)

    report = run_repair_round(
        store,
        project,
        readers=(RepairReader("other", HumanDuringRead("third", "不同")),),
        crops_dir=tmp_path / "repair",
    )
    assert not report.remaining and not report.resolved
    assert [(ref.page_index, ref.pair_index) for ref in report.already_decided] == [(0, 0)]
    assert store.latest_decision(project, 0, 0).selected_text == "人工确认"


def test_new_verification_during_repair_decision_refuses_old_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project = _disputed_page(tmp_path)
    original = store.latest_verification_id(project, 0)
    old = store.load_verification(original)
    other = SQLiteStore(tmp_path / "project.sqlite3")
    append = store.append_decision
    new_ids: list[int] = []

    def new_verification(**kwargs):
        new_ids.append(
            other.record_verification(
                old.primary_run_id, old.secondary_run_id, old.alignment, old.risks, old.candidates
            )
        )
        return append(**kwargs)

    monkeypatch.setattr(store, "append_decision", new_verification)
    with pytest.raises(ValueError, match="(?i)verification.*(latest|newer|changed)"):
        run_repair_round(
            store,
            project,
            readers=(
                RepairReader("one", FakeEngine("mineru", "鸦")),
                RepairReader("two", FakeEngine("paddleocr", "鸦")),
            ),
            crops_dir=tmp_path / "repair",
        )
    assert store.latest_decision(project, 0, 0) is None
    assert store.latest_verification_id(project, 0) == new_ids[0]
    assert len(store.list_scoped_run_ids(project, 0, "pair:0")) == 2
