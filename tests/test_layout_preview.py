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
