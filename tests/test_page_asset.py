from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.domain.page_asset import (
    PageAssetError,
    PageAssetEvidence,
    file_sha256,
    reencode_image,
    verify_page_asset,
    verify_recorded_bytes,
)


def test_page_asset_evidence_records_digest_and_size() -> None:
    data = b"some page raster bytes"
    evidence = PageAssetEvidence.from_bytes(data)

    assert evidence.sha256 == hashlib.sha256(data).hexdigest()
    assert evidence.byte_size == len(data)
    assert evidence.version == 1


def test_page_asset_evidence_rejects_bad_digest() -> None:
    with pytest.raises(ValueError, match="digest"):
        PageAssetEvidence(
            sha256="not-hex",
            byte_size=3,
        )


def test_page_asset_evidence_rejects_empty_bytes() -> None:
    with pytest.raises(ValueError, match="carry bytes"):
        PageAssetEvidence.from_bytes(b"")


def test_verify_page_asset_accepts_matching_raster(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    path.write_bytes(b"raster bytes")
    evidence = PageAssetEvidence.from_bytes(b"raster bytes")

    actual = verify_page_asset(path, evidence, what="test raster")
    assert actual == evidence.sha256


def test_verify_page_asset_rejects_missing_file(tmp_path: Path) -> None:
    evidence = PageAssetEvidence.from_bytes(b"raster bytes")
    with pytest.raises(PageAssetError, match="missing"):
        verify_page_asset(tmp_path / "nope.png", evidence, what="test raster")


def test_verify_page_asset_rejects_replaced_raster(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    path.write_bytes(b"original bytes")
    evidence = PageAssetEvidence.from_bytes(b"original bytes")

    path.write_bytes(b"replacement bytes")
    with pytest.raises(
        PageAssetError, match="not the raster this project recorded"
    ):
        verify_page_asset(path, evidence, what="test raster")


def test_verify_page_asset_without_evidence_returns_digest(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    path.write_bytes(b"legacy raster bytes")
    actual = verify_page_asset(path, None, what="legacy raster")
    assert actual == hashlib.sha256(b"legacy raster bytes").hexdigest()


def test_verify_recorded_bytes_accepts_matching() -> None:
    data = b"in-memory raster"
    evidence = PageAssetEvidence.from_bytes(data)
    verify_recorded_bytes(data, evidence, what="in-memory raster")


def test_verify_recorded_bytes_rejects_mismatch() -> None:
    evidence = PageAssetEvidence.from_bytes(b"original")
    with pytest.raises(PageAssetError, match="no longer matches"):
        verify_recorded_bytes(b"replacement", evidence, what="in-memory raster")


def test_file_sha256_streams_content(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    path.write_bytes(b"x" * (2 * 1024 * 1024))
    assert file_sha256(path) == hashlib.sha256(b"x" * (2 * 1024 * 1024)).hexdigest()


def _make_png(tmp_path: Path, name: str = "page.png") -> Path:
    from PIL import Image as PILImage

    path = tmp_path / name
    PILImage.new("RGB", (100, 150), "white").save(path, format="PNG")
    return path


def test_reencode_image_png_to_jpeg(tmp_path: Path) -> None:
    source = _make_png(tmp_path)
    original = source.read_bytes()
    data, extension = reencode_image(original, format="jpeg", quality=85)

    assert extension == "jpg"
    assert hashlib.sha256(data).hexdigest() != hashlib.sha256(original).hexdigest()

    # The re-encoded bytes round-trip through Pillow.
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"


def test_reencode_image_png_to_png_keeps_format(tmp_path: Path) -> None:
    source = _make_png(tmp_path)
    original = source.read_bytes()
    data, extension = reencode_image(original, format="png")

    assert extension == "png"
    assert hashlib.sha256(data).hexdigest() != hashlib.sha256(original).hexdigest()


def test_reencode_image_rejects_bad_format() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        reencode_image(b"not an image", format="webp")


def test_rehash_refuses_to_replace_existing_recorded_digest(tmp_path: Path) -> None:
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "page.png"
    image.write_bytes(b"original")
    original = PageAssetEvidence.from_bytes(b"original", render_parameters={"dpi": 300})
    page_id = store.add_page(pid, 0, str(image), 10, 20, page_asset=original)
    before = store.list_pages(pid)[0]
    image.write_bytes(b"replacement")
    with pytest.raises(PageAssetError, match="recorded|changed"):
        store.rehash_page_asset(page_id)
    assert store.list_pages(pid)[0] == before
    assert image.read_bytes() == b"replacement"


def test_rehash_does_not_clobber_evidence_recorded_during_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    from contextlib import closing

    from scribeforge.storage import sqlite as storage
    from scribeforge.storage.sqlite import SQLiteStore

    database = tmp_path / "project.sqlite3"
    store = SQLiteStore(database)
    store.initialize()
    pid = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "page.png"
    image.write_bytes(b"legacy image")
    page_id = store.add_page(pid, 0, str(image), 10, 20)
    legitimate = PageAssetEvidence.from_bytes(b"different concurrent recording")
    measure = storage._measure_page_asset

    def concurrent_record(path: Path) -> tuple[str, int]:
        actual = measure(path)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("""UPDATE pages SET page_asset_sha256=?, page_asset_byte_size=?,
                        page_asset_version=1, page_asset_parameters_json=? WHERE id=?""",
                       (legitimate.sha256, legitimate.byte_size, '{"source": "other"}', page_id))
        return actual

    monkeypatch.setattr(storage, "_measure_page_asset", concurrent_record)
    with pytest.raises(PageAssetError, match="changed|recorded"):
        store.rehash_page_asset(page_id)
    page = store.list_pages(pid)[0]
    assert page.page_asset_sha256 == legitimate.sha256
    assert page.page_asset_byte_size == legitimate.byte_size
    assert page.page_asset_parameters_json == '{"source": "other"}'
    assert image.read_bytes() == b"legacy image"


def test_rehash_empty_legacy_page_does_not_persist_invalid_evidence(tmp_path: Path) -> None:
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "empty.png"
    image.write_bytes(b"")
    page_id = store.add_page(pid, 0, str(image), 10, 20)
    with pytest.raises(ValueError, match="size|empty"):
        store.rehash_page_asset(page_id)
    page = store.list_pages(pid)[0]
    assert page.page_asset_sha256 is None
    assert page.page_asset_byte_size is None


def test_rehash_rejects_raster_replaced_after_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.storage import sqlite as storage
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "page.png"
    image.write_bytes(b"legacy image")
    page_id = store.add_page(pid, 0, str(image), 10, 20)
    measure = storage._measure_page_asset
    calls = 0

    def replace_after_measure(path: Path) -> tuple[str, int]:
        nonlocal calls
        result = measure(path)
        calls += 1
        if calls == 1:
            path.write_bytes(b"replacement")
        return result

    monkeypatch.setattr(storage, "_measure_page_asset", replace_after_measure)
    with pytest.raises(PageAssetError, match="changed"):
        store.rehash_page_asset(page_id)
    assert store.list_pages(pid)[0].page_asset_sha256 is None
    assert image.read_bytes() == b"replacement"


def test_batch_rehash_refuses_evidence_added_after_page_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    from contextlib import closing

    from scribeforge.storage.sqlite import SQLiteStore

    database = tmp_path / "project.sqlite3"
    store = SQLiteStore(database)
    store.initialize()
    pid = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "page.png"
    data = b"legacy image"
    image.write_bytes(data)
    page_id = store.add_page(pid, 0, str(image), 10, 20)
    rehash = store.rehash_page_asset

    def record_before_rehash(*args, **kwargs):
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("UPDATE pages SET page_asset_sha256=?, page_asset_byte_size=?, "
                       "page_asset_version=1 WHERE id=?",
                       (hashlib.sha256(data).hexdigest(), len(data), page_id))
        return rehash(*args, **kwargs)

    monkeypatch.setattr(store, "rehash_page_asset", record_before_rehash)
    with pytest.raises(PageAssetError, match="changed|recorded"):
        store.rehash_missing_page_assets()
    assert store.list_pages(pid)[0].page_asset_sha256 == hashlib.sha256(data).hexdigest()


def test_rehash_matching_existing_record_is_read_only(tmp_path: Path) -> None:
    from scribeforge.storage.sqlite import SQLiteStore

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    pid = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "page.png"
    data = b"original raster"
    image.write_bytes(data)
    evidence = PageAssetEvidence.from_bytes(data, render_parameters={"dpi": 300}, version=2)
    page_id = store.add_page(pid, 0, str(image), 10, 20, page_asset=evidence)
    before = store.list_pages(pid)[0]
    assert store.rehash_page_asset(page_id) == evidence
    assert store.list_pages(pid)[0] == before


def test_rehash_refuses_page_path_changed_during_measurement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    from contextlib import closing

    from scribeforge.storage import sqlite as storage
    from scribeforge.storage.sqlite import SQLiteStore

    database = tmp_path / "project.sqlite3"
    store = SQLiteStore(database)
    store.initialize()
    pid = store.create_project("book", "book.pdf", "hash")
    original = tmp_path / "original.png"
    replacement = tmp_path / "replacement.png"
    original.write_bytes(b"original")
    replacement.write_bytes(b"replacement")
    page_id = store.add_page(pid, 0, str(original), 10, 20)
    measure = storage._measure_page_asset
    changed = False

    def relocate(path: Path) -> tuple[str, int]:
        nonlocal changed
        result = measure(path)
        if not changed:
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("UPDATE pages SET source_image=? WHERE id=?", (str(replacement), page_id))
            changed = True
        return result

    monkeypatch.setattr(storage, "_measure_page_asset", relocate)
    with pytest.raises(PageAssetError, match="changed"):
        store.rehash_page_asset(page_id)
    page = store.list_pages(pid)[0]
    assert page.source_image == str(replacement)
    assert page.page_asset_sha256 is None
    assert original.read_bytes() == b"original"
    assert replacement.read_bytes() == b"replacement"


@pytest.mark.parametrize("column,value", [
    ("page_asset_byte_size", None),
    ("page_asset_byte_size", 999),
    ("page_asset_parameters_json", None),
    ("page_asset_parameters_json", "invalid json"),
    ("page_asset_parameters_json", "[]"),
    ("page_asset_version", None),
])
def test_rehash_does_not_invent_incomplete_recorded_metadata(
    tmp_path: Path, column: str, value: object
) -> None:
    import sqlite3
    from contextlib import closing

    from scribeforge.storage.sqlite import SQLiteStore

    database = tmp_path / "project.sqlite3"
    store = SQLiteStore(database)
    store.initialize()
    pid = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "page.png"
    image.write_bytes(b"page")
    page_id = store.add_page(pid, 0, str(image), 10, 20,
                             page_asset=PageAssetEvidence.from_bytes(b"page"))
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(f"UPDATE pages SET {column}=? WHERE id=?", (value, page_id))
    before = store.list_pages(pid)[0]
    with pytest.raises(PageAssetError, match="recorded evidence"):
        store.rehash_page_asset(page_id)
    assert store.list_pages(pid)[0] == before


@pytest.mark.parametrize("column,value", [
    ("page_asset_byte_size", 17),
    ("page_asset_parameters_json", '{"dpi": 300}'),
    ("page_asset_version", 2),
])
def test_rehash_does_not_overwrite_partial_evidence_without_digest(
    tmp_path: Path, column: str, value: object
) -> None:
    import sqlite3
    from contextlib import closing

    from scribeforge.storage.sqlite import SQLiteStore

    database = tmp_path / "project.sqlite3"
    store = SQLiteStore(database)
    store.initialize()
    project = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "page.png"
    image.write_bytes(b"legacy raster")
    page_id = store.add_page(project, 0, str(image), 10, 20)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(f"UPDATE pages SET {column}=? WHERE id=?", (value, page_id))
    before = store.list_pages(project)[0]
    with pytest.raises(PageAssetError, match="(?i)(incomplete|invalid)"):
        store.rehash_page_asset(page_id)
    assert store.list_pages(project)[0] == before
    with pytest.raises(PageAssetError, match="(?i)(incomplete|invalid)"):
        store.rehash_missing_page_assets()
    assert store.list_pages(project)[0] == before
    assert image.read_bytes() == b"legacy raster"


def test_rehash_does_not_overwrite_partial_evidence_created_while_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    from contextlib import closing

    from scribeforge.storage import sqlite as storage
    from scribeforge.storage.sqlite import SQLiteStore

    database = tmp_path / "project.sqlite3"
    store = SQLiteStore(database)
    store.initialize()
    project = store.create_project("book", "book.pdf", "hash")
    image = tmp_path / "page.png"
    image.write_bytes(b"legacy raster")
    page_id = store.add_page(project, 0, str(image), 10, 20)
    measure = storage._measure_page_asset
    changed = False

    def concurrent_partial(path: Path) -> tuple[str, int]:
        nonlocal changed
        result = measure(path)
        if not changed:
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("UPDATE pages SET page_asset_parameters_json=? WHERE id=?",
                           ('{"dpi": 300}', page_id))
            changed = True
        return result

    monkeypatch.setattr(storage, "_measure_page_asset", concurrent_partial)
    with pytest.raises(PageAssetError, match="(?i)(changed|incomplete|invalid)"):
        store.rehash_page_asset(page_id)
    page = store.list_pages(project)[0]
    assert page.page_asset_sha256 is None
    assert page.page_asset_parameters_json == '{"dpi": 300}'


def test_verify_page_asset_rejects_recorded_size_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "page.png"
    data = b"raster"
    path.write_bytes(data)
    evidence = PageAssetEvidence(sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data) + 1)
    with pytest.raises(PageAssetError, match="(?i)(size|length)"):
        verify_page_asset(path, evidence, what="page image")
    with pytest.raises(PageAssetError, match="(?i)(size|length)"):
        verify_recorded_bytes(data, evidence, what="page image")
