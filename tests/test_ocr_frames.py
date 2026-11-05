"""OCR coordinates must stay attached to the image that produced them."""

from __future__ import annotations

import hashlib
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


@pytest.mark.parametrize("use_preprocess", [False, True])
@pytest.mark.parametrize("raises", [False, True])
def test_worker_cannot_overwrite_page_or_preprocessed_evidence(
    tmp_path: Path, use_preprocess: bool, raises: bool
) -> None:
    store, project, page = setup_page(tmp_path)
    original = Path(page.source_image).read_bytes()

    class WritingEngine(Engine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            Image.new("RGB", (200, 200), "red").save(image_path)
            if raises:
                raise RuntimeError("worker failed")
            return PageOCRResult(page_index, self.name, "1", ())

    settings = PreprocessSettings() if use_preprocess else None
    with pytest.raises((ValueError, RuntimeError)):
        process_page(
            store,
            project,
            page,
            primary_engine=WritingEngine("a", "甲"),
            secondary_engine=Engine("b", "乙"),
            crops_dir=tmp_path / "crops",
            preprocess=settings,
        )
    assert Path(page.source_image).read_bytes() == original
    if use_preprocess:
        from scribeforge.domain.page_asset import file_sha256

        record = store.load_preprocess(project, 0)
        assert record is not None
        assert file_sha256(Path(record.output_image)) == record.output_sha256
    with pytest.raises(KeyError):
        store.latest_engine_run(project, 0, "a")


def test_worker_failure_preserves_frame_for_retry_and_cleans_temporary_input(
    tmp_path: Path,
) -> None:
    store, project, page = setup_page(tmp_path)
    before = Path(page.source_image).read_bytes()

    class Broken(Engine):
        input_path: Path | None = None

        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            self.input_path = Path(image_path)
            self.input_path.write_bytes(b"broken")
            raise RuntimeError("worker failed")

    broken = Broken("a", "甲")
    with pytest.raises(RuntimeError, match="worker failed"):
        process_page(
            store,
            project,
            page,
            primary_engine=broken,
            secondary_engine=Engine("b", "乙"),
            crops_dir=tmp_path / "crops",
        )
    assert broken.input_path is not None and not broken.input_path.exists()
    assert Path(page.source_image).read_bytes() == before
    outcome = process_page(
        store,
        project,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )
    frame = store.load_ocr_frame(outcome.primary_run_id)
    assert frame is not None
    assert frame.sha256 == hashlib.sha256(before).hexdigest()


@pytest.mark.parametrize("stage", ["primary", "secondary"])
@pytest.mark.parametrize("mismatch", ["page", "engine"])
def test_page_ocr_rejects_wrong_result_identity_and_retries(
    tmp_path: Path, stage: str, mismatch: str
) -> None:
    from dataclasses import replace

    from scribeforge.domain.jobs import JobStage, JobState

    store, project, page = setup_page(tmp_path)
    other_image = tmp_path / "other.png"
    Image.new("RGB", (200, 200), "blue").save(other_image)
    store.add_page(project, 1, str(other_image), 200, 200)
    original_page = Path(page.source_image).read_bytes()
    other_bytes = other_image.read_bytes()

    class WrongIdentity(Engine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            result = super().analyze_page(image_path, page_index)
            if mismatch == "page":
                return replace(result, page_index=1)
            return replace(result, engine="wrong-engine")

    primary = WrongIdentity("a", "甲") if stage == "primary" else Engine("a", "甲")
    secondary = WrongIdentity("b", "乙") if stage == "secondary" else Engine("b", "乙")
    with pytest.raises(ValueError, match="(?i)(engine|page).*(mismatch|expected)"):
        process_page(store, project, page, primary_engine=primary,
                     secondary_engine=secondary, crops_dir=tmp_path / "crops")

    expected_stage = JobStage.PRIMARY_OCR if stage == "primary" else JobStage.SECONDARY_OCR
    job = next(job for job in store.list_jobs(project) if job.stage == expected_stage)
    assert job.state is JobState.FAILED
    assert job.attempts == 1
    assert job.last_error
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        runs = db.execute("SELECT p.page_index, r.engine FROM engine_runs r "
                          "JOIN pages p ON p.id=r.page_id ORDER BY r.id").fetchall()
        frames = db.execute("SELECT count(*) FROM ocr_input_frames").fetchone()[0]
    assert runs == ([] if stage == "primary" else [(0, "a")])
    assert frames == len(runs)
    assert Path(page.source_image).read_bytes() == original_page
    assert other_image.read_bytes() == other_bytes

    outcome = process_page(store, project, page, primary_engine=Engine("a", "甲"),
                           secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
    job = next(job for job in store.list_jobs(project) if job.stage == expected_stage)
    assert job.state is JobState.SUCCEEDED
    assert job.attempts == 2
    assert store.load_ocr_run(outcome.primary_run_id).engine == "a"
    assert store.load_ocr_run(outcome.secondary_run_id).engine == "b"
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        runs = db.execute("SELECT p.page_index, r.engine FROM engine_runs r "
                          "JOIN pages p ON p.id=r.page_id ORDER BY r.id").fetchall()
    assert runs == [(0, "a"), (0, "b")]


def test_wrong_ocr_identity_cannot_be_hidden_by_an_older_matching_run(tmp_path: Path) -> None:
    from dataclasses import replace

    from scribeforge.domain.jobs import JobStage, JobState
    from scribeforge.pipeline.ocr_frame import capture_frame

    store, project, page = setup_page(tmp_path)
    frame = capture_frame(Path(page.source_image), "original", page.width, page.height)
    old_id = store.record_ocr(project, PageOCRResult(0, "a", "old", ()), frame=frame)

    class WrongEngine(Engine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            return replace(super().analyze_page(image_path, page_index), engine="other")

    with pytest.raises(ValueError, match="(?i)engine.*mismatch"):
        process_page(store, project, page, primary_engine=WrongEngine("a", "甲"),
                     secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
    job = next(job for job in store.list_jobs(project) if job.stage is JobStage.PRIMARY_OCR)
    assert job.state is JobState.FAILED
    assert store.latest_engine_run(project, 0, "a") == old_id
    with pytest.raises(KeyError):
        store.latest_engine_run(project, 0, "other")
    result = process_page(store, project, page, primary_engine=Engine("a", "甲"),
                          secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
    assert result.primary_run_id != old_id
    assert store.load_ocr_run(result.primary_run_id).lines[0].text == "甲"


def test_bad_ocr_identity_fails_only_its_page_and_book_retries(tmp_path: Path) -> None:
    from dataclasses import replace

    from scribeforge.pipeline.orchestrator import process_book

    store, project, page = setup_page(tmp_path)
    second_path = tmp_path / "page2.png"
    Image.new("RGB", (200, 200), "blue").save(second_path)
    store.add_page(project, 1, str(second_path), 200, 200)

    class WrongFirstPage(Engine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            result = super().analyze_page(image_path, page_index)
            return replace(result, page_index=1) if page_index == 0 else result

    first = process_book(store, project, primary_engine=WrongFirstPage("a", "甲"),
                         secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
    assert [p.page_index for p in first.pages] == [1]
    assert len(first.failed_pages) == 1
    assert first.failed_pages[0][0] == page.page_index
    assert "page mismatch" in first.failed_pages[0][1]
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        runs = db.execute("SELECT p.page_index, r.engine FROM engine_runs r "
                          "JOIN pages p ON p.id=r.page_id ORDER BY r.id").fetchall()
    assert runs == [(1, "a"), (1, "b")]
    retry = process_book(store, project, primary_engine=Engine("a", "甲"),
                         secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
    assert not retry.failed_pages
    assert [p.page_index for p in retry.pages] == [0, 1]
    assert retry.pages[1].primary_run_id == first.pages[0].primary_run_id
    assert retry.pages[1].secondary_run_id == first.pages[0].secondary_run_id


@pytest.mark.parametrize("entrypoint", ["process", "preprocess"])
@pytest.mark.parametrize("stale_field", ["project", "source", "geometry", "evidence", "index"])
def test_page_pipeline_rejects_page_not_matching_persisted_project_record(
    tmp_path: Path, entrypoint: str, stale_field: str
) -> None:
    from dataclasses import replace

    from scribeforge.domain.page_asset import PageAssetEvidence
    from scribeforge.pipeline.preprocessing import preprocess_page

    store, project, page = setup_page(tmp_path)
    other_path = tmp_path / "other.png"
    Image.new("RGB", (200, 200), "blue").save(other_path)
    other_project = store.create_project("other", "other.pdf", "other-hash")
    store.add_page(other_project, 0, str(other_path), 200, 200,
                   page_asset=PageAssetEvidence.from_bytes(other_path.read_bytes()))
    store.add_page(project, 1, str(other_path), 200, 200)
    if stale_field == "project":
        passed_page = store.list_pages(other_project)[0]
    elif stale_field == "source":
        passed_page = replace(page, source_image=str(other_path))
    elif stale_field == "geometry":
        passed_page = replace(page, width=page.width + 1)
    elif stale_field == "evidence":
        passed_page = replace(page, page_asset_sha256="a" * 64)
    else:
        passed_page = replace(page, page_index=1)
    before = [Path(page.source_image).read_bytes(), other_path.read_bytes()]
    primary, secondary = Engine("a", "甲"), Engine("b", "乙")
    with pytest.raises(ValueError, match="(?i)page.*(record|project|changed|match)"):
        if entrypoint == "process":
            process_page(store, project, passed_page, primary_engine=primary,
                         secondary_engine=secondary, crops_dir=tmp_path / "crops")
        else:
            preprocess_page(store, project, passed_page, PreprocessSettings(),
                            assets_dir=tmp_path / "preprocessed")
    assert not primary.images and not secondary.images
    assert not store.list_jobs(project)
    assert store.load_preprocess(project, 0) is None
    assert store.load_preprocess(project, 1) is None
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM engine_runs").fetchone()[0] == 0
    assert [Path(page.source_image).read_bytes(), other_path.read_bytes()] == before
    assert not (tmp_path / "crops").exists()
    assert not (tmp_path / "preprocessed").exists()
    fresh_page = store.list_pages(project)[0]
    if entrypoint == "process":
        outcome = process_page(store, project, fresh_page, primary_engine=primary,
                               secondary_engine=secondary, crops_dir=tmp_path / "crops")
        assert store.load_ocr_run(outcome.primary_run_id).page_index == 0
    else:
        result = preprocess_page(store, project, fresh_page, PreprocessSettings(),
                                 assets_dir=tmp_path / "preprocessed")
        assert result.image_path.is_file()


@pytest.mark.parametrize("entrypoint", ["process", "preprocess"])
def test_page_pipeline_refuses_stale_page_after_legacy_rehash(
    tmp_path: Path, entrypoint: str
) -> None:
    from scribeforge.pipeline.preprocessing import preprocess_page

    store, project, stale = setup_page(tmp_path)
    assert stale.page_asset_sha256 is None
    assert store.rehash_page_asset(stale.id) is not None
    image = Path(stale.source_image)
    Image.new("RGB", (200, 200), "red").save(image)
    primary, secondary = Engine("a", "甲"), Engine("b", "乙")
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        if entrypoint == "process":
            process_page(store, project, stale, primary_engine=primary,
                         secondary_engine=secondary, crops_dir=tmp_path / "crops")
        else:
            preprocess_page(store, project, stale, PreprocessSettings(),
                            assets_dir=tmp_path / "preprocessed")
    assert not store.list_jobs(project)
    assert not primary.images and not secondary.images
    fresh = store.list_pages(project)[0]
    with pytest.raises(ValueError, match="(?i)(raster|page).*(recorded|mismatch)"):
        if entrypoint == "process":
            process_page(store, project, fresh, primary_engine=primary,
                         secondary_engine=secondary, crops_dir=tmp_path / "crops")
        else:
            preprocess_page(store, project, fresh, PreprocessSettings(),
                            assets_dir=tmp_path / "preprocessed")


def test_page_ocr_refuses_evidence_backfilled_during_frame_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.domain.jobs import JobStage, JobState
    from scribeforge.pipeline import orchestrator

    store, project, page = setup_page(tmp_path)
    capture = orchestrator.capture_frame
    original = Path(page.source_image).read_bytes()
    first = True

    def rehash_then_replace(path: Path, kind: str, width: int, height: int):
        nonlocal first
        if first:
            first = False
            store.rehash_page_asset(page.id)
            Image.new("RGB", (200, 200), "red").save(page.source_image)
        return capture(path, kind, width, height)

    monkeypatch.setattr(orchestrator, "capture_frame", rehash_then_replace)
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        process_page(store, project, page, primary_engine=Engine("a", "甲"),
                     secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
    job = next(job for job in store.list_jobs(project) if job.stage is JobStage.PRIMARY_OCR)
    assert job.state is JobState.FAILED
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM engine_runs").fetchone()[0] == 0
    Path(page.source_image).write_bytes(original)
    monkeypatch.setattr(orchestrator, "capture_frame", capture)
    fresh = store.list_pages(project)[0]
    outcome = process_page(store, project, fresh, primary_engine=Engine("a", "甲"),
                           secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
    assert store.load_ocr_run(outcome.primary_run_id).page_index == 0
    assert next(j for j in store.list_jobs(project) if j.stage is JobStage.PRIMARY_OCR).attempts == 2


def test_preprocess_refuses_evidence_backfilled_during_processing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.domain.jobs import JobStage, JobState
    from scribeforge.pipeline import preprocessing

    store, project, page = setup_page(tmp_path)
    apply = preprocessing.apply_preprocess

    def change_before_record(*args, **kwargs):
        Image.new("RGB", (200, 200), "red").save(page.source_image)
        store.rehash_page_asset(page.id)
        return apply(*args, **kwargs)

    monkeypatch.setattr(preprocessing, "apply_preprocess", change_before_record)
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        preprocessing.preprocess_page(store, project, page, PreprocessSettings(),
                                      assets_dir=tmp_path / "preprocessed")
    job = next(job for job in store.list_jobs(project) if job.stage is JobStage.PREPROCESS)
    assert job.state is JobState.FAILED
    assert store.load_preprocess(project, 0) is None
    assert not (tmp_path / "preprocessed").exists()
    monkeypatch.setattr(preprocessing, "apply_preprocess", apply)
    fresh = store.list_pages(project)[0]
    result = preprocessing.preprocess_page(store, project, fresh, PreprocessSettings(),
                                           assets_dir=tmp_path / "preprocessed")
    assert result.image_path.is_file()
    assert next(j for j in store.list_jobs(project) if j.stage is JobStage.PREPROCESS).attempts == 2


def test_page_ocr_rechecks_page_evidence_in_record_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project, page = setup_page(tmp_path)
    record_ocr = store.record_ocr

    def changed_before_insert(*args, **kwargs):
        store.rehash_page_asset(page.id)
        return record_ocr(*args, **kwargs)

    monkeypatch.setattr(store, "record_ocr", changed_before_insert)
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        process_page(store, project, page, primary_engine=Engine("a", "甲"),
                     secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM engine_runs").fetchone()[0] == 0


def test_preprocess_rechecks_page_evidence_in_record_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.pipeline.preprocessing import preprocess_page

    store, project, page = setup_page(tmp_path)
    record_preprocess = store.record_preprocess

    def changed_before_insert(*args, **kwargs):
        store.rehash_page_asset(page.id)
        return record_preprocess(*args, **kwargs)

    monkeypatch.setattr(store, "record_preprocess", changed_before_insert)
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        preprocess_page(store, project, page, PreprocessSettings(),
                        assets_dir=tmp_path / "preprocessed")
    assert store.load_preprocess(project, 0) is None


def test_guarded_storage_rejects_result_for_a_different_page(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    store.add_page(project, 1, page.source_image, 200, 200)
    with pytest.raises(ValueError, match="page record.*page index"):
        store.record_ocr(project, PageOCRResult(1, "a", "1", ()), expected_page=page)
    with pytest.raises(ValueError, match="page record.*page index"):
        store.record_preprocess(
            project, 1, settings={}, applied=(), measured_skew_degrees=0,
            output_image="unused", output_sha256="0" * 64,
            output_width=200, output_height=200, notes={}, expected_page=page,
        )
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM engine_runs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM page_preprocess").fetchone()[0] == 0


@pytest.mark.parametrize("entrypoint", ["process", "preprocess"])
@pytest.mark.parametrize("missing_column", ["page_asset_sha256", "page_asset_byte_size", "page_asset_parameters_json", "page_asset_version"])
def test_incomplete_page_asset_evidence_blocks_processing(
    tmp_path: Path, entrypoint: str, missing_column: str
) -> None:
    from scribeforge.pipeline.preprocessing import preprocess_page

    store, project, page = setup_page(tmp_path)
    assert store.rehash_page_asset(page.id) is not None
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        db.execute(f"UPDATE pages SET {missing_column}=NULL WHERE id=?", (page.id,))
    page = store.list_pages(project)[0]
    before = Path(page.source_image).read_bytes()
    primary, secondary = Engine("a", "甲"), Engine("b", "乙")
    with pytest.raises(ValueError, match="(?i)(page|asset).*(incomplete|invalid)"):
        if entrypoint == "process":
            process_page(store, project, page, primary_engine=primary,
                         secondary_engine=secondary, crops_dir=tmp_path / "crops")
        else:
            preprocess_page(store, project, page, PreprocessSettings(),
                            assets_dir=tmp_path / "preprocessed")
    assert not primary.images and not secondary.images
    assert not store.list_jobs(project)
    assert Path(page.source_image).read_bytes() == before
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM engine_runs").fetchone()[0] == 0


def test_incomplete_page_asset_evidence_blocks_repair(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    assert store.rehash_page_asset(page.id) is not None
    page = store.list_pages(project)[0]
    process_page(store, project, page, primary_engine=Engine("a", "甲"),
                 secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops",
                 preprocess=PreprocessSettings())
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        db.execute("UPDATE pages SET page_asset_byte_size=NULL WHERE id=?", (page.id,))
    repair = Engine("c", "丙")
    with pytest.raises(ValueError, match="(?i)(page|asset).*(incomplete|invalid)"):
        run_repair_round(store, project, readers=(RepairReader("c", repair),),
                         crops_dir=tmp_path / "repair")
    assert not repair.images
    assert not (tmp_path / "repair").exists()


@pytest.mark.parametrize("entrypoint", ["process", "preprocess"])
def test_wrong_recorded_page_size_blocks_processing_without_mutation(
    tmp_path: Path, entrypoint: str
) -> None:
    from scribeforge.pipeline.preprocessing import preprocess_page

    store, project, page = setup_page(tmp_path)
    store.rehash_page_asset(page.id)
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        db.execute("UPDATE pages SET page_asset_byte_size=page_asset_byte_size+1 WHERE id=?",
                   (page.id,))
    page = store.list_pages(project)[0]
    primary = Engine("a", "甲")
    with pytest.raises(ValueError, match="(?i)size"):
        if entrypoint == "process":
            process_page(store, project, page, primary_engine=primary,
                         secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops")
        else:
            preprocess_page(store, project, page, PreprocessSettings(),
                            assets_dir=tmp_path / "preprocessed")
    assert not primary.images
    assert not store.list_jobs(project)


def test_repair_reader_rejects_page_evidence_changed_during_read(
    tmp_path: Path,
) -> None:
    store, project, page = setup_page(tmp_path)
    result = process_page(store, project, page, primary_engine=Engine("a", "甲"),
                          secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops",
                          preprocess=PreprocessSettings())
    old_frame = store.load_ocr_frame(result.primary_run_id)
    assert old_frame is not None and old_frame.kind == "preprocessed"
    source = Path(page.source_image)
    original = source.read_bytes()

    class ChangingPage(Engine):
        def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
            reading = super().analyze_page(image_path, page_index)
            Image.new("RGB", (200, 200), "red").save(source)
            store.rehash_page_asset(page.id)
            return reading

    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        run_repair_round(store, project, readers=(RepairReader("c", ChangingPage("c", "丙")),),
                         crops_dir=tmp_path / "repair")
    assert store.list_scoped_run_ids(project, 0, "pair:0") == ()
    assert store.latest_decision(project, 0, 0) is None
    assert store.load_ocr_frame(result.primary_run_id) == old_frame
    assert source.read_bytes() != original


def test_vlm_rejects_page_evidence_changed_during_review(tmp_path: Path) -> None:
    from scribeforge.domain.jobs import JobStage, JobState
    from scribeforge.domain.vlm import VLMReading

    store, project, page = setup_page(tmp_path)
    source = Path(page.source_image)
    original = source.read_bytes()

    class ChangingReviewer:
        def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
            Image.new("RGB", (200, 200), "red").save(source)
            store.rehash_page_asset(page.id)
            return VLMReading(page_index, pair_index, "vlm", "1", "甲", False)

    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        process_page(store, project, page, primary_engine=Engine("a", "甲"),
                     secondary_engine=Engine("b", "乙"), reviewer=ChangingReviewer(),
                     crops_dir=tmp_path / "crops", preprocess=PreprocessSettings())
    verification_id = store.latest_verification_id(project, 0)
    assert not store.list_vlm_reviews(verification_id)
    assert store.latest_decision(project, 0, 0) is None
    job = next(job for job in store.list_jobs(project) if job.stage is JobStage.VLM_REVIEW)
    assert job.state is JobState.FAILED
    assert source.read_bytes() != original


def test_repair_vlm_rejects_page_evidence_changed_during_review(tmp_path: Path) -> None:
    from scribeforge.domain.vlm import VLMReading

    store, project, page = setup_page(tmp_path)
    outcome = process_page(store, project, page, primary_engine=Engine("a", "甲"),
                           secondary_engine=Engine("b", "乙"), crops_dir=tmp_path / "crops",
                           preprocess=PreprocessSettings())
    source = Path(page.source_image)

    class ChangingReviewer:
        def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
            Image.new("RGB", (200, 200), "red").save(source)
            store.rehash_page_asset(page.id)
            return VLMReading(page_index, pair_index, "vlm", "1", "甲", False)

    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        run_repair_round(store, project, readers=(RepairReader("c", Engine("c", "丙")),),
                         reviewer=ChangingReviewer(), crops_dir=tmp_path / "repair")
    assert len(store.list_scoped_run_ids(project, 0, "pair:0")) == 1
    assert not store.list_vlm_reviews(outcome.verification_id)
    assert store.latest_decision(project, 0, 0) is None


def test_vlm_page_snapshot_conflict_can_retry_with_fresh_page(tmp_path: Path) -> None:
    from scribeforge.domain.jobs import JobStage, JobState
    from scribeforge.domain.vlm import VLMReading

    store, project, page = setup_page(tmp_path)
    original = Path(page.source_image).read_bytes()

    class BackfillingReviewer:
        def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
            store.rehash_page_asset(page.id)
            return VLMReading(page_index, pair_index, "vlm", "1", "甲", False)

    class ValidReviewer:
        def transcribe_crop(self, image_path: str, page_index: int, pair_index: int) -> VLMReading:
            return VLMReading(page_index, pair_index, "vlm", "1", "甲", False)

    kwargs = {"primary_engine": Engine("a", "甲"), "secondary_engine": Engine("b", "乙"),
              "crops_dir": tmp_path / "crops", "preprocess": PreprocessSettings()}
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        process_page(store, project, page, reviewer=BackfillingReviewer(), **kwargs)
    verification_id = store.latest_verification_id(project, 0)
    assert not store.list_vlm_reviews(verification_id)
    fresh = store.list_pages(project)[0]
    assert fresh.page_asset_sha256 == hashlib.sha256(original).hexdigest()
    outcome = process_page(store, project, fresh, reviewer=ValidReviewer(), **kwargs)
    assert outcome.verification_id == verification_id
    assert len(store.list_vlm_reviews(verification_id)) == 1
    job = next(j for j in store.list_jobs(project) if j.stage is JobStage.VLM_REVIEW)
    assert job.state is JobState.SUCCEEDED and job.attempts == 2
    assert Path(page.source_image).read_bytes() == original


def test_adjudication_refuses_verification_from_another_project(tmp_path: Path) -> None:
    from scribeforge.pipeline.adjudication import adjudicate_verification

    store, project, page = setup_page(tmp_path)
    outcome = process_page(store, project, page, primary_engine=Engine("a", "甲"),
                           secondary_engine=Engine("b", "甲"), crops_dir=tmp_path / "crops")
    other = store.create_project("other", "other.pdf", "different")
    other_path = tmp_path / "other.png"
    Image.new("RGB", (200, 200), "red").save(other_path)
    store.add_page(other, 0, str(other_path), 200, 200)
    with pytest.raises(ValueError, match="(?i)(verification|project|page).*(match|belong)"):
        adjudicate_verification(store, other, outcome.verification_id)
    assert store.latest_decision(other, 0, 0) is None
    assert store.latest_decision(project, 0, 0).selected_text == "甲"


def test_automatic_decision_refuses_page_change_at_write_boundary_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project, page = setup_page(tmp_path)
    before = Path(page.source_image).read_bytes()
    append = store.append_decision

    def backfill_before_write(*args, **kwargs):
        store.rehash_page_asset(page.id)
        return append(*args, **kwargs)

    monkeypatch.setattr(store, "append_decision", backfill_before_write)
    kwargs = {"primary_engine": Engine("a", "甲"), "secondary_engine": Engine("b", "甲"),
              "crops_dir": tmp_path / "crops"}
    with pytest.raises(ValueError, match="(?i)page.*(record|changed|match)"):
        process_page(store, project, page, **kwargs)
    assert store.latest_decision(project, 0, 0) is None
    primary_id = store.latest_engine_run(project, 0, "a")
    verification_id = store.latest_verification_id(project, 0)
    monkeypatch.setattr(store, "append_decision", append)
    fresh = store.list_pages(project)[0]
    outcome = process_page(store, project, fresh, **kwargs)
    assert outcome.primary_run_id == primary_id
    assert outcome.verification_id == verification_id
    assert store.latest_decision(project, 0, 0).selected_text == "甲"
    assert Path(page.source_image).read_bytes() == before


def test_guarded_decision_rejects_wrong_verification_page_or_pair(tmp_path: Path) -> None:
    store, project, page = setup_page(tmp_path)
    outcome = process_page(store, project, page, primary_engine=Engine("a", "甲"),
                           secondary_engine=Engine("b", "甲"), crops_dir=tmp_path / "crops")
    other = store.create_project("other", "other.pdf", "different")
    other_image = tmp_path / "other.png"
    Image.new("RGB", (200, 200), "red").save(other_image)
    store.add_page(other, 0, str(other_image), 200, 200)
    other_page = store.list_pages(other)[0]
    with pytest.raises(ValueError, match="verification.*(page|pair)"):
        store.append_decision(project_id=other, page_index=0, pair_index=0,
                              selected_text="甲", actor="automatic", evidence_refs=(),
                              expected_page=other_page, expected_verification_id=outcome.verification_id)
    assert store.latest_decision(other, 0, 0) is None
    with pytest.raises(ValueError, match="verification.*(page|pair)"):
        store.append_decision(project_id=project, page_index=0, pair_index=9,
                              selected_text="甲", actor="automatic", evidence_refs=(),
                              expected_page=page, expected_verification_id=outcome.verification_id)
    assert store.latest_decision(project, 0, 9) is None
