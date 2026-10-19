from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.adapters.runtime_workers import (
    BackendNotReadyError,
    build_runtime_engine,
    require_backend_ready,
    runtime_environment,
    worker_command,
)
from scribeforge.runtime.models import RuntimeLayout


def _layout(tmp_path: Path) -> RuntimeLayout:
    return RuntimeLayout(root=tmp_path / "runtime")


def test_worker_command_resolves_backend_python_per_os(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    script = tmp_path / "worker.py"

    assert worker_command(layout, "mineru", script, "windows") == (
        str(layout.root / "envs" / "mineru" / "Scripts" / "python.exe"),
        str(script),
    )
    assert worker_command(layout, "paddleocr", script, "linux") == (
        str(layout.root / "envs" / "paddleocr" / "bin" / "python"),
        str(script),
    )


def test_runtime_environment_merges_model_and_cache_dirs(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    environment = runtime_environment(layout, {"PADDLE_HOME": "override"})

    assert environment["MINERU_HOME"] == str(layout.models_dir / "mineru")
    assert environment["PADDLE_HOME"] == "override"


def test_require_backend_ready_rejects_missing_and_not_ready(tmp_path: Path) -> None:
    layout = _layout(tmp_path)

    with pytest.raises(BackendNotReadyError, match="not ready"):
        require_backend_ready(layout, "mineru", "windows", lambda backend: "missing")

    with pytest.raises(BackendNotReadyError, match="missing"):
        require_backend_ready(layout, "mineru", "windows", lambda backend: "ready")


def test_require_backend_ready_accepts_installed_backend(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    python = layout.root / "envs" / "mineru" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")

    require_backend_ready(layout, "mineru", "windows", lambda backend: "ready")


def test_build_runtime_engine_fails_fast_when_backend_not_ready(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    script = tmp_path / "worker.py"

    with pytest.raises(BackendNotReadyError, match="not ready"):
        build_runtime_engine(
            layout, "mineru", script, "windows",
            name="mineru", status=lambda backend: "failed",
        )


def test_build_runtime_engine_rechecks_health_before_every_page(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    python = layout.root / "envs" / "mineru" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    script = tmp_path / "worker.py"
    script.write_text("", encoding="utf-8")

    state = {"value": "ready"}
    engine = build_runtime_engine(
        layout, "mineru", script, "windows",
        name="mineru", status=lambda backend: state["value"],
    )

    state["value"] = "failed"
    with pytest.raises(BackendNotReadyError, match="not ready"):
        engine.analyze_page(str(tmp_path / "page.png"), 0)
