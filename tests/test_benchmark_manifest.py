import json
from pathlib import Path

import pytest

from scribeforge.benchmark.manifest import load_manifest


def test_manifest_loads_versioned_page_cases(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "zh-clear-001",
                        "image": "fixtures/zh-clear-001.png",
                        "ground_truth": "fixtures/zh-clear-001.txt",
                        "language": "zh",
                        "tags": ["clear", "body"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(path)

    assert manifest.schema_version == 1
    assert manifest.cases[0].id == "zh-clear-001"
    assert manifest.cases[0].tags == ("clear", "body")


def test_manifest_rejects_duplicate_case_ids(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    case = {
        "id": "duplicate",
        "image": "a.png",
        "ground_truth": "a.txt",
        "language": "zh",
        "tags": [],
    }
    path.write_text(json.dumps({"schema_version": 1, "cases": [case, case]}), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate benchmark case id"):
        load_manifest(path)


def test_manifest_rejects_unknown_schema_version(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": 99, "cases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported benchmark schema"):
        load_manifest(path)


def _write(tmp_path: Path, **case: object) -> Path:
    payload = {
        "id": "c1",
        "image": "page.png",
        "ground_truth": "truth.txt",
        "language": "zh",
        "tags": [],
    }
    payload.update(case)
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema_version": 1, "cases": [payload]}), encoding="utf-8"
    )
    return path


def test_manifest_keeps_an_explicit_direction(tmp_path) -> None:
    manifest = load_manifest(_write(tmp_path, direction="vertical-rl"))

    assert manifest.cases[0].direction == "vertical-rl"


def test_manifest_defaults_to_no_direction(tmp_path) -> None:
    """No declaration is not a horizontal declaration."""

    manifest = load_manifest(_write(tmp_path))

    assert manifest.cases[0].direction is None


def test_manifest_rejects_an_unknown_direction(tmp_path) -> None:
    with pytest.raises(ValueError, match="unknown benchmark direction"):
        load_manifest(_write(tmp_path, direction="right-to-left"))


def test_manifest_loads_ground_truth_review_metadata(tmp_path) -> None:
    path = _write(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ground_truth_review_status"] = "single-pass"
    payload["ground_truth_note"] = "A single transcription; not independently proofread."
    path.write_text(json.dumps(payload), encoding="utf-8")

    manifest = load_manifest(path)

    assert manifest.ground_truth_review_status == "single-pass"
    assert manifest.ground_truth_note == "A single transcription; not independently proofread."


def test_manifest_accepts_double_proofread_status(tmp_path) -> None:
    path = _write(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ground_truth_review_status"] = "double-proofread"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_manifest(path).ground_truth_review_status == "double-proofread"


def test_manifest_leaves_legacy_review_status_undeclared(tmp_path) -> None:
    manifest = load_manifest(_write(tmp_path))

    assert manifest.ground_truth_review_status is None
    assert manifest.ground_truth_note is None


def test_manifest_rejects_non_string_ground_truth_review_status(tmp_path) -> None:
    path = _write(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ground_truth_review_status"] = 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TypeError, match="ground_truth_review_status must be a string"):
        load_manifest(path)


def test_manifest_rejects_unknown_ground_truth_review_status(tmp_path) -> None:
    path = _write(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ground_truth_review_status"] = "gold"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown ground-truth review status"):
        load_manifest(path)


def test_manifest_rejects_non_string_ground_truth_note(tmp_path) -> None:
    path = _write(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["ground_truth_note"] = ["reviewer 1", "reviewer 2"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(TypeError, match="ground_truth_note must be a string"):
        load_manifest(path)
