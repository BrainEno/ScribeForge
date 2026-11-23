"""Original-frame inspection is not human confirmation or image export."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.cli import run
from scribeforge.domain.alignment import LineAlignment, PageAlignment
from scribeforge.domain.ocr import BlockType, BoundingBox, OCRBlock, OCRLine, PageOCRResult
from scribeforge.domain.page_asset import PageAssetEvidence
from scribeforge.domain.risk import PairRisk
from scribeforge.pipeline.layout_preview import create_layout_preview
from scribeforge.storage.sqlite import OCRInputFrame, SQLiteStore


@pytest.fixture
def sample(tmp_path: Path):
    path = tmp_path / "source.png"
    image = Image.new("RGB", (100, 80), "white")
    for x in range(20, 60):
        for y in range(16, 40):
            image.putpixel((x, y), (10, 20, 30))
    image.save(path)
    data = path.read_bytes()
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    pid = store.create_project("Book", "source.pdf", "hash")
    store.add_page(pid, 0, str(path), 100, 80, page_asset=PageAssetEvidence.from_bytes(data))
    line = OCRLine("text", BoundingBox(0, 0, 0.2, 0.1))
    figure = OCRBlock(BlockType.FIGURE, BoundingBox(0.2, 0.2, 0.4, 0.3), 0, 0)
    frame = OCRInputFrame("original", hashlib.sha256(data).hexdigest(), 100, 80)
    primary = store.record_ocr(pid, PageOCRResult(0, "one", "1", (line,), (figure,)), frame=frame)
    secondary = store.record_ocr(pid, PageOCRResult(0, "two", "1", (line,), (figure,)))
    vid = store.record_verification(
        primary,
        secondary,
        PageAlignment(0, "one", "two", (LineAlignment(0, 0, 1, 0, ()),)),
        (PairRisk(0, 0, ()),),
        (),
    )
    return store, pid, primary, secondary, vid, path


def _preview(tmp_path, sample, **kwargs):
    _store, pid, primary, _, _, _ = sample
    args = {
        "project_id": pid,
        "page_index": 0,
        "run_id": primary,
        "block_index": 0,
        "output": tmp_path / "preview.png",
    }
    args.update(kwargs)
    return create_layout_preview(tmp_path / "project.sqlite3", **args)


def test_original_frame_preview_records_provenance_and_never_modifies_store(tmp_path, sample):
    db = tmp_path / "project.sqlite3"
    before = db.read_bytes()
    result = _preview(tmp_path, sample)
    assert result["status"] == "unverified_preview"
    assert result["pixel_box"] == [20, 16, 60, 40]
    assert result["verification_id"] == sample[4]
    assert result["source_sha256"] == hashlib.sha256(sample[5].read_bytes()).hexdigest()
    data = (tmp_path / "preview.png").read_bytes()
    assert result["preview_sha256"] == hashlib.sha256(data).hexdigest()
    with Image.open(tmp_path / "preview.png") as image:
        assert image.getpixel((0, 0)) == (10, 20, 30)
    assert db.read_bytes() == before
    with pytest.raises(FileExistsError):
        _preview(tmp_path, sample)
    assert (tmp_path / "preview.png").read_bytes() == data


def test_reject_unknown_frame_wrong_scope_and_invalid_output(tmp_path, sample):
    for args, message in [
        ({"run_id": sample[3]}, "unknown or preprocessed"),
        ({"run_id": 123456}, "latest verification"),
        ({"page_index": 1}, "physical page"),
        ({"block_index": 9}, "block index"),
        ({"block_index": -1}, "invalid"),
        ({"output": tmp_path / "x.jpg"}, "png"),
    ]:
        with pytest.raises(ValueError, match=message):
            _preview(tmp_path, sample, **args)
    assert not (tmp_path / "preview.png").exists()


def test_changed_source_refuses_and_preserves_raster(tmp_path, sample):
    path = sample[5]
    Image.new("RGB", (100, 80), "black").save(path)
    data = path.read_bytes()
    with pytest.raises(ValueError, match="differs from recorded"):
        _preview(tmp_path, sample)
    assert path.read_bytes() == data
    assert not (tmp_path / "preview.png").exists()


def test_cli_reports_preview_and_refuses_existing_file(tmp_path, sample, capsys):
    _, pid, primary, _, vid, _ = sample
    args = [
        "layout-preview",
        "--store",
        str(tmp_path),
        "--project",
        str(pid),
        "--page",
        "0",
        "--run",
        str(primary),
        "--block",
        "0",
        "--out",
        str(tmp_path / "result.png"),
    ]
    assert run(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verification_id"] == vid
    assert "not human confirmation" in result["notice"]
    assert run(args) == 2
    assert "could not create layout preview" in capsys.readouterr().err


def test_frame_mismatch_and_preprocessed_are_not_mapped(tmp_path, sample):
    import sqlite3

    db = tmp_path / "project.sqlite3"
    for kind, digest, width in [
        ("original", "f" * 64, 100),
        ("original", "a" * 64, 99),
        ("preprocessed", "a" * 64, 100),
    ]:
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE ocr_input_frames SET kind=?, sha256=?, width=? WHERE run_id=?",
                (kind, digest, width, sample[2]),
            )
        with pytest.raises(ValueError, match="OCR frame"):
            _preview(tmp_path, sample)
        assert not (tmp_path / "preview.png").exists()


def test_nonfigure_observation_is_not_a_preview_candidate(tmp_path, sample):
    import sqlite3

    with sqlite3.connect(tmp_path / "project.sqlite3") as conn:
        conn.execute("UPDATE ocr_blocks SET block_type='text' WHERE run_id=?", (sample[2],))
    with pytest.raises(ValueError, match="only figure and table"):
        _preview(tmp_path, sample)
    assert not (tmp_path / "preview.png").exists()


def test_preview_rejects_unknown_import_digest(tmp_path, sample):
    import sqlite3

    with sqlite3.connect(tmp_path / "project.sqlite3") as conn:
        conn.execute(
            "UPDATE pages SET page_asset_sha256=NULL, page_asset_byte_size=NULL, "
            "page_asset_parameters_json=NULL, page_asset_version=NULL WHERE project_id=?",
            (sample[1],),
        )
    with pytest.raises(ValueError, match="no recorded import digest"):
        _preview(tmp_path, sample)


def test_failed_preview_write_removes_partial_file(tmp_path, sample, monkeypatch):
    output = tmp_path / "preview.png"
    original_open = Path.open

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, data):
            self.stream.write(data[:5])
            raise OSError("disk full")

    def failing_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        if path == output and mode == "xb":
            return FailingWriter(stream)
        return stream

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError, match="disk full"):
        _preview(tmp_path, sample)
    assert not output.exists()


def test_empty_and_nonfinite_boxes_rejected():
    from scribeforge.pipeline.layout_preview import _pixel_box

    with pytest.raises(ValueError, match="positive"):
        _pixel_box(BoundingBox(0, 0, 0, 0.5), 100, 100)
    with pytest.raises(ValueError, match="finite"):
        _pixel_box(BoundingBox(float("nan"), 0, 0.5, 0.5), 100, 100)
    assert _pixel_box(BoundingBox(0.001, 0.001, 0.005, 0.005), 100, 100) == (0, 0, 1, 1)


def test_context_is_same_snapshot_and_does_not_change_store(tmp_path, sample):
    database = tmp_path / "project.sqlite3"
    before = database.read_bytes()
    context = tmp_path / "context.png"
    result = _preview(tmp_path, sample, context_output=context)
    overview = result["context"]
    assert overview["output"] == str(context)
    assert overview["size"] == [100, 80]
    assert overview["sha256"] == hashlib.sha256(context.read_bytes()).hexdigest()
    with Image.open(context) as image:
        assert image.size == (100, 80)
        assert image.getpixel((0, 0)) == (255, 255, 255)
        assert image.getpixel((20, 16)) == (10, 20, 30)
    assert database.read_bytes() == before


def test_context_is_scaled_but_crop_coordinates_remain_original(tmp_path, sample):
    import sqlite3

    path = sample[5]
    Image.new("RGB", (2000, 1000), "white").save(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    with sqlite3.connect(tmp_path / "project.sqlite3") as conn:
        conn.execute(
            "UPDATE pages SET width=2000, height=1000, page_asset_sha256=?, "
            "page_asset_byte_size=? WHERE project_id=?",
            (digest, len(data), sample[1]),
        )
        conn.execute(
            "UPDATE ocr_input_frames SET width=2000, height=1000, sha256=? WHERE run_id=?",
            (digest, sample[2]),
        )
    result = _preview(tmp_path, sample, context_output=tmp_path / "context.png")
    assert result["frame_size"] == [2000, 1000]
    assert result["pixel_box"] == [400, 200, 1200, 500]
    assert result["context"]["size"] == [1600, 800]


def test_context_existing_file_and_duplicate_paths_never_overwrite(tmp_path, sample):
    context = tmp_path / "existing.png"
    context.write_bytes(b"important")
    with pytest.raises(FileExistsError):
        _preview(tmp_path, sample, context_output=context)
    assert context.read_bytes() == b"important"
    assert not (tmp_path / "preview.png").exists()
    with pytest.raises(ValueError, match="different files"):
        _preview(tmp_path, sample, context_output=tmp_path / "preview.png")
    with pytest.raises(ValueError, match="context output"):
        _preview(tmp_path, sample, context_output=tmp_path / "x.jpg")
    assert context.read_bytes() == b"important"


def test_context_write_failure_cleans_both_outputs(tmp_path, sample, monkeypatch):
    output, context = tmp_path / "preview.png", tmp_path / "context.png"
    original_open = Path.open

    class BrokenFile:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, data):
            self.stream.write(data[:8])
            raise OSError("context disk full")

    def failing_open(path, mode="r", *args, **kwargs):
        stream = original_open(path, mode, *args, **kwargs)
        return BrokenFile(stream) if path == context and mode == "xb" else stream

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError, match="context disk full"):
        _preview(tmp_path, sample, context_output=context)
    assert not output.exists()
    assert not context.exists()


def test_cli_context_and_legacy_output_contract(tmp_path, sample, capsys):
    _, pid, primary, _, _, _ = sample
    args = [
        "layout-preview",
        "--store",
        str(tmp_path),
        "--project",
        str(pid),
        "--page",
        "0",
        "--run",
        str(primary),
        "--block",
        "0",
        "--out",
        str(tmp_path / "crop.png"),
        "--context-out",
        str(tmp_path / "page.png"),
    ]
    assert run(args) == 0
    response = json.loads(capsys.readouterr().out)
    assert response["context"]["size"] == [100, 80]
    assert (tmp_path / "crop.png").is_file()
    assert (tmp_path / "page.png").is_file()


def test_context_rejected_with_stale_verification_without_writing_any_file(tmp_path, sample):
    store, pid, _, secondary, _, _ = sample
    page = store.load_ocr_run(secondary)
    newer = store.record_ocr(pid, page)
    store.record_verification(
        newer,
        secondary,
        PageAlignment(0, "two", "two", (LineAlignment(0, 0, 1, 0, ()),)),
        (PairRisk(0, 0, ()),),
        (),
    )
    with pytest.raises(ValueError, match="latest verification"):
        _preview(tmp_path, sample, context_output=tmp_path / "context.png")
    assert not (tmp_path / "preview.png").exists()
    assert not (tmp_path / "context.png").exists()


def test_context_decode_failure_does_not_leave_files(tmp_path, sample, monkeypatch):
    def fail_open(*args, **kwargs):
        raise OSError("decoder I/O error")

    monkeypatch.setattr(Image, "open", fail_open)
    with pytest.raises(OSError, match="decoder I/O error"):
        _preview(tmp_path, sample, context_output=tmp_path / "context.png")
    assert not (tmp_path / "preview.png").exists()
    assert not (tmp_path / "context.png").exists()
