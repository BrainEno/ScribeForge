from pathlib import Path

import pytest

from scribeforge.runtime.manager import RuntimeInstallError, RuntimeManager
from scribeforge.runtime.models import InstallStep, RuntimeBackendPlan, RuntimePlan


class RecordingExecutor:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.fail_on = fail_on

    def run(self, argv: tuple[str, ...], env: dict[str, str]) -> None:
        self.calls.append((argv, env))
        if self.fail_on and self.fail_on in argv:
            raise RuntimeError("boom")


def _backend(root: Path, name: str = "mineru") -> RuntimeBackendPlan:
    return RuntimeBackendPlan(
        name=name,
        environment_dir=root / "envs" / name,
        acceleration="cpu",
        steps=(InstallStep("install", (name, "install")),),
        model_steps=(InstallStep("download", (name, "download")),),
        health_checks=(InstallStep("verify", (name, "verify")),),
        environment=(("SCRIBE_CACHE", str(root / "cache")),),
    )


def test_install_runs_packages_models_then_health_and_emits_progress(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    events = []
    manager = RuntimeManager(tmp_path, executor, on_event=events.append)
    plan = RuntimePlan(tmp_path, (_backend(tmp_path),))

    manager.install(plan)

    assert [call[0] for call in executor.calls] == [
        ("mineru", "install"),
        ("mineru", "download"),
        ("mineru", "verify"),
    ]
    assert all(call[1]["SCRIBE_CACHE"] == str(tmp_path / "cache") for call in executor.calls)
    assert manager.status("mineru").state == "ready"
    assert [
        (event.phase, event.state, event.step, event.completed_steps, event.total_steps)
        for event in events
    ] == [
        ("install", "started", None, 0, 3),
        ("install", "step_started", "install", 0, 3),
        ("install", "step_completed", "install", 1, 3),
        ("install", "completed", None, 1, 3),
        ("models", "started", None, 1, 3),
        ("models", "step_started", "download", 1, 3),
        ("models", "step_completed", "download", 2, 3),
        ("models", "completed", None, 2, 3),
        ("health", "started", None, 2, 3),
        ("health", "step_started", "verify", 2, 3),
        ("health", "step_completed", "verify", 3, 3),
        ("health", "completed", None, 3, 3),
        ("complete", "ready", None, 3, 3),
    ]


def test_health_failure_is_persisted_with_phase_and_step(tmp_path: Path) -> None:
    manager = RuntimeManager(tmp_path, RecordingExecutor(fail_on="verify"))

    with pytest.raises(RuntimeInstallError, match="mineru:health:verify"):
        manager.install(RuntimePlan(tmp_path, (_backend(tmp_path),)))

    status = manager.status("mineru")
    assert status.state == "failed"
    assert status.failed_step == "health:verify"


def test_repair_only_replays_requested_backend(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    manager = RuntimeManager(tmp_path, executor)
    plan = RuntimePlan(tmp_path, (_backend(tmp_path, "mineru"), _backend(tmp_path, "paddleocr")))

    manager.repair(plan, "paddleocr")

    assert all(call[0][0] == "paddleocr" for call in executor.calls)
    assert manager.status("paddleocr").state == "ready"
    assert manager.status("mineru").state == "missing"


def test_repair_rejects_unknown_backend(tmp_path: Path) -> None:
    manager = RuntimeManager(tmp_path, RecordingExecutor())

    with pytest.raises(KeyError, match="missing"):
        manager.repair(RuntimePlan(tmp_path, (_backend(tmp_path),)), "missing")
