from pathlib import Path

import pytest

from scribeforge.runtime.manager import RuntimeInstallError, RuntimeManager
from scribeforge.runtime.models import InstallStep, RuntimeBackendPlan, RuntimePlan


class RecordingExecutor:
    def __init__(self, fail_on: int | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail_on = fail_on

    def run(self, argv: tuple[str, ...]) -> None:
        self.calls.append(argv)
        if self.fail_on == len(self.calls):
            raise RuntimeError("boom")


def _plan(root: Path) -> RuntimePlan:
    backend = RuntimeBackendPlan(
        name="mineru",
        environment_dir=root / "envs" / "mineru",
        acceleration="cpu",
        steps=(
            InstallStep("prepare", ("uv", "python", "install", "3.12")),
            InstallStep("install", ("uv", "pip", "install", "mineru>=4.0,<5")),
        ),
    )
    return RuntimePlan(root=root, backends=(backend,))


def test_manager_executes_steps_in_order_and_marks_backend_ready(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    manager = RuntimeManager(tmp_path, executor)

    manager.install(_plan(tmp_path))

    assert executor.calls == [
        ("uv", "python", "install", "3.12"),
        ("uv", "pip", "install", "mineru>=4.0,<5"),
    ]
    assert manager.status("mineru").state == "ready"


def test_manager_stops_on_failure_and_records_failed_state(tmp_path: Path) -> None:
    executor = RecordingExecutor(fail_on=2)
    manager = RuntimeManager(tmp_path, executor)

    with pytest.raises(RuntimeInstallError, match="mineru:install"):
        manager.install(_plan(tmp_path))

    assert len(executor.calls) == 2
    assert manager.status("mineru").state == "failed"
    assert manager.status("mineru").failed_step == "install"
