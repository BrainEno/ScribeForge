import sqlite3
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.vlm import VLMReading
from scribeforge.pipeline.orchestrator import process_page
from scribeforge.pipeline.rounds import RepairReader, run_repair_round
from scribeforge.storage.sqlite import SQLiteStore


class Engine:
    def __init__(self, name, text):
        self.name, self.text = name, text

    def analyze_page(self, image_path, page_index):
        return PageOCRResult(
            page_index, self.name, "1", (OCRLine(self.text, BoundingBox(0.1, 0.1, 0.5, 0.1)),)
        )


class Reviewer:
    def transcribe_crop(self, image_path, page_index, pair_index):
        return VLMReading(page_index, pair_index, "vlm", "1", "不确定", True)


def project(store, root, name, color):
    pid = store.create_project(name, name, "hash")
    path = root / f"{name}.png"
    Image.new("RGB", (100, 100), color).save(path)
    store.add_page(pid, 0, str(path), 100, 100)
    return pid, store.list_pages(pid)[0]


def test_shared_crop_directory_preserves_prior_project_evidence(tmp_path: Path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    first, page1 = project(store, tmp_path, "one", "white")
    second, page2 = project(store, tmp_path, "two", "black")
    a = process_page(
        store,
        first,
        page1,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    review1 = store.list_vlm_reviews(a.verification_id)[0]
    original = Path(review1.crop_path).read_bytes()
    b = process_page(
        store,
        second,
        page2,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    review2 = store.list_vlm_reviews(b.verification_id)[0]
    assert review1.crop_path != review2.crop_path
    assert Path(review1.crop_path).read_bytes() == original


def test_crop_runs_and_vlm_bind_geometry_atomically(tmp_path: Path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    result = process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    run_repair_round(
        store,
        pid,
        readers=(RepairReader("c", Engine("c", "丙")),),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    with sqlite3.connect(tmp_path / "db.sqlite3") as db:
        rows = db.execute(
            "SELECT ocr_run_id, vlm_review_id, frame_sha256, crop_sha256, "
            "x, y, width, height, scale, crop_path FROM crop_input_evidence ORDER BY id"
        ).fetchall()
    assert len(rows) == 3
    assert sum(row[0] is not None for row in rows) == 1
    assert sum(row[1] is not None for row in rows) == 2
    assert all(row[2] == store.load_ocr_frame(result.primary_run_id).sha256 for row in rows)
    assert all(len(row[3]) == 64 and row[6] > 0 and row[7] > 0 and row[8] >= 1 for row in rows)
    assert all(Path(row[9]).is_file() for row in rows)


def test_reader_changes_crop_before_return_is_not_persisted(tmp_path: Path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )

    class Mutator(Engine):
        def analyze_page(self, image_path, page_index):
            Image.new("RGB", (10, 10), "red").save(image_path)
            return super().analyze_page(image_path, page_index)

    with pytest.raises(ValueError, match="crop.*changed"):
        run_repair_round(
            store,
            pid,
            readers=(RepairReader("c", Mutator("c", "丙")),),
            crops_dir=tmp_path / "crops",
        )
    assert store.list_scoped_run_ids(pid, 0, "pair:0") == ()


def test_vlm_mutating_crop_is_not_persisted(tmp_path: Path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")

    class BadReviewer(Reviewer):
        def transcribe_crop(self, image_path, page_index, pair_index):
            Image.new("RGB", (4, 4), "red").save(image_path)
            return super().transcribe_crop(image_path, page_index, pair_index)

    with pytest.raises(ValueError, match="crop.*changed"):
        process_page(
            store,
            pid,
            page,
            primary_engine=Engine("a", "甲"),
            secondary_engine=Engine("b", "乙"),
            reviewer=BadReviewer(),
            crops_dir=tmp_path / "crops",
        )
    verification = store.latest_verification_id(pid, 0)
    assert not store.list_vlm_reviews(verification)


def test_backup_relocates_crop_evidence_and_vlm_path(tmp_path: Path):
    from scribeforge.project_backup import _remap_page_assets

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    result = process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    before = store.list_vlm_reviews(result.verification_id)[0]
    relative = "crops/" + Path(before.crop_path).name
    target = tmp_path / "restore"
    _remap_page_assets(tmp_path / "db.sqlite3", target, {relative})
    after = store.list_vlm_reviews(result.verification_id)[0]
    assert after.crop_path == str((target / relative).resolve())
    with sqlite3.connect(tmp_path / "db.sqlite3") as db:
        assert (
            db.execute("SELECT crop_path FROM crop_input_evidence").fetchone()[0] == after.crop_path
        )


def test_resume_rejects_tampered_recorded_vlm_crop(tmp_path: Path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    first = process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    evidence = store.list_vlm_reviews(first.verification_id)[0]
    Path(evidence.crop_path).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="crop.*changed"):
        process_page(
            store,
            pid,
            page,
            primary_engine=Engine("a", "甲"),
            secondary_engine=Engine("b", "乙"),
            reviewer=Reviewer(),
            crops_dir=tmp_path / "crops",
        )


def test_bad_crop_metadata_cannot_attach_to_another_page(tmp_path: Path):
    from scribeforge.storage.sqlite import CropInputEvidence

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    one, page = project(store, tmp_path, "one", "white")
    two, _ = project(store, tmp_path, "two", "black")
    result = process_page(
        store,
        one,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )
    frame = store.load_ocr_frame(result.primary_run_id)
    input_data = CropInputEvidence(
        result.verification_id, 0, frame.sha256, "1" * 64, "crop.png", 0, 0, 10, 10, 1
    )
    with pytest.raises(ValueError, match="crop.*page"):
        store.record_ocr(
            two, Engine("c", "丙").analyze_page("ignored", 0), scope="pair:0", crop_input=input_data
        )
    assert store.list_scoped_run_ids(two, 0, "pair:0") == ()


def test_each_reader_uses_own_crop_so_later_mutation_cannot_damage_prior_evidence(tmp_path: Path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )

    class Mutator(Engine):
        def analyze_page(self, image_path, page_index):
            Image.new("RGB", (10, 10), "red").save(image_path)
            return super().analyze_page(image_path, page_index)

    with pytest.raises(ValueError, match="crop.*changed"):
        run_repair_round(
            store,
            pid,
            readers=(RepairReader("c", Engine("c", "丙")), RepairReader("d", Mutator("d", "丁"))),
            crops_dir=tmp_path / "crops",
        )
    with sqlite3.connect(tmp_path / "db.sqlite3") as db:
        row = db.execute(
            "SELECT crop_path, crop_sha256 FROM crop_input_evidence WHERE ocr_run_id IS NOT NULL"
        ).fetchone()
    from hashlib import sha256

    assert sha256(Path(row[0]).read_bytes()).hexdigest() == row[1]


def test_v13_migration_does_not_invent_crop_evidence(tmp_path: Path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    result = process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    review = store.list_vlm_reviews(result.verification_id)[0]
    store.close()
    with sqlite3.connect(tmp_path / "db.sqlite3") as db:
        db.execute("DROP TABLE crop_input_evidence")
        db.execute("PRAGMA user_version=13")
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    assert store.load_vlm_crop_input(review.id) is None
    assert store.list_vlm_reviews(result.verification_id)[0].reading.text == "不确定"
    with pytest.raises(ValueError, match="crop input is unknown"):
        process_page(
            store,
            pid,
            page,
            primary_engine=Engine("a", "甲"),
            secondary_engine=Engine("b", "乙"),
            reviewer=Reviewer(),
            crops_dir=tmp_path / "crops",
        )


def test_review_json_hides_crop_with_unknown_legacy_geometry(tmp_path: Path, capsys):
    import json

    from scribeforge.cli import run

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    result = process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    with sqlite3.connect(tmp_path / "project.sqlite3") as db:
        db.execute("DELETE FROM crop_input_evidence")
    assert store.list_vlm_reviews(result.verification_id)
    assert run(["review", "--store", str(tmp_path), "--json"]) == 0
    item = json.loads(capsys.readouterr().out)["projects"][0]["items"][0]
    assert item["crop_path"] is None


def test_backup_restore_then_resume_preserves_verified_crop(tmp_path: Path):
    from hashlib import sha256

    from scribeforge.project_backup import create_project_backup, restore_project_backup

    root = tmp_path / "store"
    assets = root / "assets"
    assets.mkdir(parents=True)
    source = tmp_path / "book.pdf"
    source.write_bytes(b"pdf source")
    store = SQLiteStore(root / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", str(source), sha256(source.read_bytes()).hexdigest())
    page_path = assets / "page.png"
    Image.new("RGB", (100, 100), "white").save(page_path)
    store.add_page(pid, 0, str(page_path), 100, 100)
    result = process_page(
        store,
        pid,
        store.list_pages(pid)[0],
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=root / "crops",
    )
    archive = tmp_path / "backup.zip"
    create_project_backup(root, archive)
    target = tmp_path / "restored"
    restore_project_backup(archive, target)
    restored = SQLiteStore(target / "project.sqlite3")
    restored.initialize()
    review = restored.list_vlm_reviews(result.verification_id)[0]
    assert Path(review.crop_path).is_relative_to(target)
    assert Path(review.crop_path).is_file()
    process_page(
        restored,
        pid,
        restored.list_pages(pid)[0],
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=target / "crops",
    )


def test_retry_after_changed_crop_preserves_failed_artifact(tmp_path: Path):
    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")

    class BadReviewer(Reviewer):
        def transcribe_crop(self, image_path, page_index, pair_index):
            Image.new("RGB", (4, 4), "red").save(image_path)
            return super().transcribe_crop(image_path, page_index, pair_index)

    with pytest.raises(ValueError, match="crop.*changed"):
        process_page(
            store,
            pid,
            page,
            primary_engine=Engine("a", "甲"),
            secondary_engine=Engine("b", "乙"),
            reviewer=BadReviewer(),
            crops_dir=tmp_path / "crops",
        )
    failed_path = next((tmp_path / "crops").glob("*.png"))
    failed_bytes = failed_path.read_bytes()
    result = process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        reviewer=Reviewer(),
        crops_dir=tmp_path / "crops",
    )
    assert failed_path.read_bytes() == failed_bytes
    review = store.list_vlm_reviews(result.verification_id)[0]
    assert Path(review.crop_path) != failed_path


def test_desktop_overlay_uses_preprocessed_frame_not_raw_image(tmp_path: Path):
    from scribeforge.domain.preprocess import PreprocessOperation, PreprocessSettings
    from scribeforge.ui.viewmodel import load_pair_view, load_review_rows

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
        preprocess=PreprocessSettings(
            operations=(PreprocessOperation.CROP_BORDER,), adapt_to_page=False
        ),
    )
    view = load_pair_view(store, pid, load_review_rows(store, pid)[0], crops_dir=tmp_path / "crops")
    assert view.frame_kind == "preprocessed"
    assert view.frame_image == Path(store.load_preprocess(pid, 0).output_image)
    assert view.page_image == Path(page.source_image)


def test_desktop_disables_overlay_for_unknown_legacy_frame(tmp_path: Path):
    from scribeforge.ui.viewmodel import load_pair_view, load_review_rows

    store = SQLiteStore(tmp_path / "db.sqlite3")
    store.initialize()
    pid, page = project(store, tmp_path, "one", "white")
    result = process_page(
        store,
        pid,
        page,
        primary_engine=Engine("a", "甲"),
        secondary_engine=Engine("b", "乙"),
        crops_dir=tmp_path / "crops",
    )
    with sqlite3.connect(tmp_path / "db.sqlite3") as db:
        db.execute(
            "DELETE FROM ocr_input_frames WHERE run_id IN (?, ?)",
            (result.primary_run_id, result.secondary_run_id),
        )
    view = load_pair_view(store, pid, load_review_rows(store, pid)[0], crops_dir=tmp_path / "crops")
    assert view.frame_kind == "unknown"
    assert view.frame_image is None
