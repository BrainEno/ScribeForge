from __future__ import annotations

import io
import json
from pathlib import Path
from typing import cast

import pytest

from scribeforge.cli import run
from scribeforge.runtime.environment import build_runtime_environment
from scribeforge.runtime.location import (
    BINDING_FILENAME,
    LocationError,
    RuntimeLocation,
    external_model_location,
    location_report,
    model_location_scope,
)
from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan
from scribeforge.runtime.sizes import required_bytes
from scribeforge.workers.paddle_model_setup import PADDLE_MODELS


def _models(root: Path) -> Path:
    paths = [root / "mineru"] + [root / "paddleocr" / "verified" / m.name for m in PADDLE_MODELS]
    for path in paths:
        path.mkdir(parents=True)
        (path / "fixture").touch()
    return root


def test_preflight_does_not_create_candidate_and_budget_discounts_external_weights(
    tmp_path: Path,
) -> None:
    models = _models(tmp_path / "old models")
    root = tmp_path / "new env"
    report = location_report(root, models, "linux")
    assert report["runtime_root"] == str(root.resolve())
    assert report["external_models"] is True
    assert report["backends"] == {"mineru": "missing", "paddleocr": "missing"}
    disk = cast(dict[str, object], report["disk"])
    assert disk["required_bytes"] == required_bytes(("mineru", "paddleocr"), reuse_models=True)
    assert not root.exists()
    assert {item.name for item in tmp_path.iterdir()} == {models.name}
    assert required_bytes(("mineru", "paddleocr"), reuse_models=True) < required_bytes(
        ("mineru", "paddleocr")
    )


def test_unsafe_overlapping_project_and_unrelated_paths_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    with pytest.raises(LocationError, match="separate directory"):
        RuntimeLocation.resolve(root, root / "models")
    with pytest.raises(LocationError, match="absolute"):
        RuntimeLocation.resolve(Path("relative"), None)
    root.mkdir()
    (root / "notes.txt").touch()
    with pytest.raises(LocationError, match="dedicated"):
        location_report(root, None, "linux")
    (root / "project.sqlite3").touch()
    with pytest.raises(LocationError, match="book project"):
        location_report(root / "new-runtime", None, "linux")


def test_binding_blocks_conflicting_selection_and_survives_reopen(tmp_path: Path) -> None:
    models = _models(tmp_path / "models")
    location = RuntimeLocation.resolve(tmp_path / "runtime", models)
    with location.configuration_lock():
        location.claim()
        with pytest.raises(LocationError, match="Another process"), location.configuration_lock():
            pytest.fail("the runtime lock was acquired twice")
    assert RuntimeLocation.from_bound_root(location.root) == location
    with pytest.raises(LocationError, match="different model location"):
        location_report(location.root, None, "linux")
    with pytest.raises(LocationError, match="outside existing runtimes"):
        location_report(location.root / "child", None, "linux")
    (location.root / BINDING_FILENAME).write_text("not a record", encoding="utf-8")
    with pytest.raises(LocationError, match="Cannot read"):
        RuntimeLocation.from_bound_root(location.root)


def test_install_plan_does_not_download_external_core_weights(tmp_path: Path) -> None:
    models = _models(tmp_path / "other models")
    layout = RuntimeLayout(tmp_path / "runtime")
    hardware = HardwareProfile("windows", "x86_64", None, None, None, None)
    with model_location_scope(models):
        plan = build_runtime_plan(hardware, layout, "uv")
        assert plan.backend("mineru").model_steps == ()
        assert plan.backend("paddleocr").model_steps == ()
        assert plan.backend("surya").model_steps
        assert dict(plan.backend("mineru").environment)["MINERU_HOME"] == str(models / "mineru")
        assert dict(plan.backend("paddleocr").environment)["SCRIBEFORGE_PADDLE_MODELS_ROOT"] == str(
            models / "paddleocr" / "verified"
        )
        assert plan.backend("paddleocr").health_checks[-1].name == "sample-ocr"
    assert external_model_location() is None
    assert build_runtime_environment(layout)["MINERU_HOME"] == str(layout.models_dir / "mineru")


def test_cli_reports_missing_external_models_as_structured_failure(tmp_path: Path) -> None:
    output = io.StringIO()
    assert (
        run(
            [
                "--model-location",
                str(tmp_path / "missing"),
                "runtime-location-check",
                "--runtime-root",
                str(tmp_path / "runtime"),
            ],
            stdout=output,
        )
        != 0
    )
    assert json.loads(output.getvalue())["code"] == "missing_models"


def test_cli_cleanup_rejects_external_models_even_without_explicit_flag(tmp_path: Path) -> None:
    models = _models(tmp_path / "models")
    location = RuntimeLocation.resolve(tmp_path / "runtime", models)
    with location.configuration_lock():
        location.claim()
    output = io.StringIO()
    assert (
        run(
            ["runtime-clean", "--runtime-root", str(location.root), "--models", "--json"],
            stdout=output,
        )
        != 0
    )
    assert list(models.iterdir())
    assert external_model_location() is None
