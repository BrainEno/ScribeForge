from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribeforge.cli import run
from scribeforge.export_receipt import (
    FileFingerprint,
    fingerprint,
    make_export_receipt,
    normalization_path,
    receipt_path,
    verify_export_artifact,
)

_SOURCE = "a" * 64


def _fixture(root: Path, extension: str = "epub") -> Path:
    path = root / f"成品.{extension}"
    # Byte verification deliberately does not unzip or parse the book.
    path.write_bytes(b"sample output")
    artifact = fingerprint(path)
    normalization = json.dumps(
        {"policy": "preserve", "output": {"file": path.name, "sha256": artifact.sha256}}
    ).encode()
    normalization_path(path).write_bytes(normalization)
    receipt_path(path).write_bytes(
        make_export_receipt(
            path,
            artifact=artifact,
            normalization=FileFingerprint.from_bytes(normalization),
            source_sha256=_SOURCE,
            project_id=2,
            title="Book",
            timestamp="2026-11-20T00:00:00+00:00",
            strict_acceptance=True,
            include_page_furniture=False,
            policy="preserve",
            pages=4,
            accepted_pairs=20,
        )
    )
    return path


def _codes(report):
    return {item["code"] for item in report["findings"]}


@pytest.mark.parametrize("extension", ["epub", "docx"])
def test_verifier_can_compare_local_receipt_and_source_without_writes(
    tmp_path: Path, capsys, extension
) -> None:
    artifact = _fixture(tmp_path, extension)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert run(["verify-export", f"--file={artifact}", "--source-sha256", _SOURCE]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "consistent" and report["project_source_matches"] is True
    assert report["receipt"]["accepted_pairs"] == 20
    assert report["format"] == extension
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    mismatch = verify_export_artifact(artifact, expected_source="b" * 64)
    assert mismatch["status"] == "mismatch" and mismatch["project_source_matches"] is False
    assert "project_source_mismatch" in _codes(mismatch)


def test_changed_contents_and_a_changed_sidecar_are_distinct(tmp_path: Path) -> None:
    path = _fixture(tmp_path)
    normalization = normalization_path(path)
    normalization.write_bytes(normalization.read_bytes() + b"\n")
    check = verify_export_artifact(path)
    assert check["status"] == "mismatch" and check["normalization_matches_artifact"]
    assert _codes(check) == {"normalization_digest_mismatch"}
    path.write_bytes(b"edited output")
    assert {"artifact_digest_mismatch", "normalization_artifact_mismatch"}.issubset(
        _codes(verify_export_artifact(path))
    )


def test_old_exports_and_missing_companion_files(tmp_path: Path) -> None:
    path = _fixture(tmp_path)
    normalization_path(path).unlink()
    assert verify_export_artifact(path)["status"] == "mismatch"
    path = _fixture(tmp_path)
    receipt_path(path).unlink()
    legacy = verify_export_artifact(path, expected_source=_SOURCE)
    assert legacy["status"] == "legacy_match" and legacy["project_source_matches"] is None
    normalization_path(path).unlink()
    assert verify_export_artifact(path)["status"] == "no_receipt"


def test_invalid_receipt_and_bad_arguments_fail_closed(tmp_path: Path, capsys) -> None:
    path = _fixture(tmp_path)
    receipt_path(path).write_text('{"schema_version": true, "kind": "scribeforge_trusted_export"}')
    report = verify_export_artifact(path)
    assert report["status"] == "mismatch" and "receipt_invalid" in _codes(report)
    assert run(["verify-export", "--file", str(path), "--source-sha256", "bad"]) == 2
    assert "expected project source" in capsys.readouterr().err
    assert run(["verify-export", "--file", str(tmp_path / "file.pdf")]) == 2
    assert "choose an EPUB or DOCX" in capsys.readouterr().err
