import json

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
