from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.cli import run
from scribeforge.domain.alignment import PageAlignment
from scribeforge.domain.ocr import PageOCRResult
from scribeforge.domain.page_asset import PageAssetEvidence
from scribeforge.pipeline.assembly import (
    PageImageError,
    UnresolvedContentError,
    assemble_verified_content,
)
from scribeforge.pipeline.textless_review import submit_textless_confirmation, textless_preview
from scribeforge.storage.sqlite import SQLiteStore


def _fixture(root: Path) -> tuple[SQLiteStore, int, Path, int, int, int]:
    asset = root / "scan.png"
    Image.new("RGB", (40, 80), "white").save(asset)
    store = SQLiteStore(root / "project.sqlite3")
    store.initialize()
    project = store.create_project("Book", "book.pdf", "source")
    store.add_page(
        project, 0, str(asset), 40, 80, page_asset=PageAssetEvidence.from_bytes(asset.read_bytes())
    )
    primary = store.record_ocr(project, PageOCRResult(0, "primary", "1", ()))
    secondary = store.record_ocr(project, PageOCRResult(0, "secondary", "1", ()))
    verification = _rerun(store, primary, secondary)
    return store, project, asset, primary, secondary, verification


def _rerun(store: SQLiteStore, primary: int, secondary: int) -> int:
    return store.record_verification(
        primary, secondary, PageAlignment(0, "primary", "secondary", ()), (), ()
    )


def test_textless_cli_rechecks_preview_and_audits_original_bytes(tmp_path: Path, capsys) -> None:
    store, project, asset, _, _, verification = _fixture(tmp_path)
    scope = ["--store", str(tmp_path), "--project-id", str(project), "--page", "0"]
    database = tmp_path / "project.sqlite3"
    before = database.read_bytes()
    assert run(["textless-preview", *scope]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert database.read_bytes() == before
    assert base64.b64decode(shown["raster_base64"]) == asset.read_bytes()
    token = shown["confirmation_token"]
    assert token.endswith(f":v{verification}:{shown['raster_sha256']}")
    command = [
        "confirm",
        *scope,
        "--confirmation-token",
        token,
        "--kind",
        "blank",
        "--reason=checked",
        "--json",
    ]
    assert run(command) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["page_index"] == 0 and receipt["kind"] == "blank"
    assert run(command) == 2
    assert "already has a confirmation" in capsys.readouterr().err
    history = textless_preview(store, project, 0)
    assert history["confirmation_token"] is None
    (row,) = history["history"]
    assert row["binding"] == "current" and row["id"] == receipt["id"]
    assert assemble_verified_content(store, project).confirmed_blank_pages == (0,)
    asset.write_bytes(b"tampered")
    with pytest.raises(PageImageError, match="not the raster"):
        assemble_verified_content(store, project)


def test_guarded_confirmation_cannot_cross_verification_batches(tmp_path: Path) -> None:
    store, project, _, primary, secondary, _ = _fixture(tmp_path)
    preview = textless_preview(store, project, 0)
    token = preview["confirmation_token"]
    _rerun(store, primary, secondary)
    with pytest.raises(ValueError, match="changed"):
        submit_textless_confirmation(store, project, 0, token=token, kind="blank", reason="checked")
    current = textless_preview(store, project, 0)
    submit_textless_confirmation(
        store,
        project,
        0,
        token=current["confirmation_token"],
        kind="non_text",
        reason="illustration",
    )
    assert len(assemble_verified_content(store, project).images) == 1
    _rerun(store, primary, secondary)
    with pytest.raises(UnresolvedContentError) as error:
        assemble_verified_content(store, project)
    assert error.value.unconfirmed_pages == (0,)
    following = textless_preview(store, project, 0)
    assert following["history"][0]["binding"] == "historical"
    assert following["confirmation_token"] is not None


def test_confirm_rechecks_latest_verification_inside_the_write(tmp_path: Path, monkeypatch) -> None:
    store, project, _, primary, secondary, _ = _fixture(tmp_path)
    token = textless_preview(store, project, 0)["confirmation_token"]
    write = store.record_page_confirmation

    def concurrent_rerun(**kwargs):
        _rerun(store, primary, secondary)
        return write(**kwargs)

    monkeypatch.setattr(store, "record_page_confirmation", concurrent_rerun)
    with pytest.raises(ValueError, match="verification changed"):
        submit_textless_confirmation(store, project, 0, token=token, kind="blank", reason="checked")
    assert not store.list_page_confirmations(project, 0)
