"""Repair rounds: re-read a disputed line, accept only what readers agree on."""

from __future__ import annotations

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


class FakeEngine:
    """Deterministic engine whose answer can change between calls."""

    def __init__(self, name: str, *texts: str) -> None:
        self._name = name
        self._texts = list(texts)
        self.calls = 0
        self.last_image = ""

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        self.calls += 1
        self.last_image = image_path
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
    with Image.open(first_crop) as before, Image.open(second_crop) as after:
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
        readers=(RepairReader("crop", FakeEngine("mineru", "鸦")),),
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
    assert any(ref.startswith("vlm_review:") and ref != "vlm_review:unknown"
               for ref in decision.evidence_refs)


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
        readers=(RepairReader("crop", FakeEngine("mineru", "鸦")),),
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
        run_repair_round(
            store, project_id, readers=(), crops_dir=tmp_path / "repair"
        )


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
