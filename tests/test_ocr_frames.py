"""OCR coordinates must stay attached to the image that produced them."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.preprocess import PreprocessOperation, PreprocessSettings
from scribeforge.pipeline.orchestrator import process_page
from scribeforge.pipeline.rounds import RepairReader, run_repair_round
from scribeforge.storage.sqlite import SQLiteStore


class Engine:
    def __init__(self, name: str, text: str) -> None:
        self.name, self.text = name, text
        self.images: list[bytes] = []

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        self.images.append(Path(image_path).read_bytes())
        return PageOCRResult(
            page_index, self.name, "1", (OCRLine(self.text, BoundingBox(0.1, 0.1, 0.5, 0.1)),)
        )


def setup_page(tmp_path: Path):
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project = store.create_project("book", "book.pdf", "hash")
    image = Image.new("RGB", (200, 200), "black")
    ImageDraw.Draw(image).rectangle((30, 30, 169, 169), fill="white")
    path = tmp_path / "page.png"
    image.save(path)
    store.add_page(project, 0, str(path), 200, 200)
    return store, project, store.list_pages(project)[0]


def test_repair_uses_ocr_frame_after_restart(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    settings = PreprocessSettings(
        operations=(PreprocessOperation.CROP_BORDER,), adapt_to_page=False
    )
    result = process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
        preprocess=settings,
    )
    frame = store.load_ocr_frame(result.primary_run_id)
    assert frame is not None and frame.kind == "preprocessed"
    assert frame.width < page.width and frame.height < page.height
    store.close()
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    reader = Engine("c", "丙")
    run_repair_round(
        store, project, readers=(RepairReader("c", reader),), crops_dir=tmp_path / "repair"
    )
    with Image.open(next((tmp_path / "repair").glob("*.png"))) as crop:
        # Crop from the white inner frame; the raw page would include black border.
        assert crop.convert("L").getextrema()[0] > 200


def test_legacy_unknown_frame_is_not_guessed(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    result = process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )
    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute(
            "DELETE FROM ocr_input_frames WHERE run_id IN (?, ?)",
            (result.primary_run_id, result.secondary_run_id),
        )
    with pytest.raises(ValueError, match="frame.*unknown"):
        run_repair_round(
            store,
            project,
            readers=(RepairReader("c", Engine("c", "丙")),),
            crops_dir=tmp_path / "repair",
        )
    assert store.latest_decision(project, 0, 0) is None


def test_resume_cannot_switch_from_raw_to_preprocessed_frame(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )
    with pytest.raises(ValueError, match="frame"):
        process_page(
            store,
            project,
            page,
            primary_engine=Engine("a", "甲"),
            secondary_engine=Engine("b", "乙"),
            crops_dir=tmp_path / "crops",
            preprocess=PreprocessSettings(),
        )


def test_tampered_preprocessed_frame_refuses_repair(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
        preprocess=PreprocessSettings(),
    )
    record = store.load_preprocess(project, 0)
    assert record is not None
    Image.new("RGB", (record.output_width, record.output_height), "red").save(record.output_image)
    engine = Engine("c", "丙")
    with pytest.raises(ValueError, match="frame.*mismatch"):
        run_repair_round(
            store, project, readers=(RepairReader("c", engine),), crops_dir=tmp_path / "repair"
        )
    assert not engine.images


def test_mixed_recorded_frames_are_refused(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    result = process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )
    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute(
            "UPDATE ocr_input_frames SET sha256 = ? WHERE run_id = ?",
            ("0" * 64, result.secondary_run_id),
        )
    with pytest.raises(ValueError, match="frame mismatch"):
        run_repair_round(
            store,
            project,
            readers=(RepairReader("c", Engine("c", "丙")),),
            crops_dir=tmp_path / "repair",
        )


def test_worker_that_changes_its_input_cannot_record_evidence(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)

    class ChangingEngine(Engine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            result = super().analyze_page(image_path, page_index)
            Image.new("RGB", (200, 200), "red").save(image_path)
            return result

    with pytest.raises(ValueError, match="frame.*changed"):
        process_page(
            store,
            project,
            page,
            primary_engine=ChangingEngine("a", "甲"),
            secondary_engine=Engine("b", "乙"),
            crops_dir=tmp_path / "crops",
        )
    with pytest.raises(KeyError):
        store.latest_engine_run(project, 0, "a")


def test_review_json_labels_coordinate_frame(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from scribeforge.cli import run

    store, project, page = setup_page(tmp_path)
    process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
        preprocess=PreprocessSettings(),
    )
    assert run(["review", "--store", str(tmp_path), "--json"]) == 0
    item = json.loads(capsys.readouterr().out)["projects"][0]["items"][0]
    assert item["coordinate_frame"] == "preprocessed"
    assert item["ocr_frame_image_path"] == store.load_preprocess(project, 0).output_image
    assert item["source_image_path"] == page.source_image


def test_v12_migration_keeps_old_runs_unknown(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    outcome = process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )
    store.close()
    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute("DROP TABLE ocr_input_frames")
        connection.execute("PRAGMA user_version=12")
    restored = SQLiteStore(tmp_path / "project.sqlite3")
    restored.initialize()
    assert restored.load_ocr_frame(outcome.primary_run_id) is None
    assert restored.load_ocr_run(outcome.primary_run_id).lines[0].text == "甲"
    with pytest.raises(ValueError, match="frame.*unknown"):
        run_repair_round(
            restored,
            project,
            readers=(RepairReader("c", Engine("c", "丙")),),
            crops_dir=tmp_path / "repair",
        )


def test_review_json_does_not_claim_unknown_legacy_frame(tmp_path: Path, capsys) -> None:
    import json

    from scribeforge.cli import run

    store, project, page = setup_page(tmp_path)
    outcome = process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )
    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute(
            "DELETE FROM ocr_input_frames WHERE run_id IN (?, ?)",
            (outcome.primary_run_id, outcome.secondary_run_id),
        )
    assert run(["review", "--store", str(tmp_path), "--json"]) == 0
    item = json.loads(capsys.readouterr().out)["projects"][0]["items"][0]
    assert item["coordinate_frame"] == "unknown"
    assert item["ocr_frame_image_path"] is None
    assert item["crop_path"] is None


def test_vlm_and_repair_read_the_same_preprocessed_frame(tmp_path: Path) -> None:
    from io import BytesIO

    from scribeforge.domain.vlm import VLMReading

    class Reviewer:
        def __init__(self) -> None:
            self.crops: list[bytes] = []

        def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
            self.crops.append(Path(image_path).read_bytes())
            return VLMReading(page_index, pair_index, "vlm", "1", "甲", True)

    store, project, page = setup_page(tmp_path)
    reviewer = Reviewer()
    settings = PreprocessSettings(
        operations=(PreprocessOperation.CROP_BORDER,), adapt_to_page=False
    )
    process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
        preprocess=settings,
        reviewer=reviewer,
    )
    assert reviewer.crops
    with Image.open(BytesIO(reviewer.crops[0])) as crop:
        assert crop.convert("L").getextrema()[0] > 200
    run_repair_round(
        store,
        project,
        readers=(RepairReader("c", Engine("c", "丙")),),
        reviewer=reviewer,
        crops_dir=tmp_path / "repair",
    )
    assert len(reviewer.crops) == 2
    with Image.open(BytesIO(reviewer.crops[1])) as crop:
        assert crop.convert("L").getextrema()[0] > 200
