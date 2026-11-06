from __future__ import annotations

import time
from pathlib import Path

import pytest

from scribeforge.domain.cancellation import ProcessingCancelled
from scribeforge.runtime.manager import (
    RuntimeCommandTimeoutError,
    RuntimeEvent,
    RuntimeInstallError,
    RuntimeManager,
)
from scribeforge.runtime.models import InstallStep, RuntimeBackendPlan, RuntimePlan


class CancelledExecutor:
    def run(
        self,
        argv: tuple[str, ...],
        env: dict[str, str] | None = None,
        *,
        timeout_seconds: float,
    ) -> None:
        raise ProcessingCancelled("processing cancelled by user")


def _model_plan(root: Path) -> RuntimePlan:
    backend = RuntimeBackendPlan(
        name="mineru",
        environment_dir=root / "envs" / "mineru",
        acceleration="cpu",
        steps=(),
        model_steps=(
            InstallStep("download", ("tool", "download"), timeout_seconds=12.5),
        ),
    )
    return RuntimePlan(root, (backend,))


def test_cancelled_setup_stops_without_retry_and_persists_retryable_state(
    tmp_path: Path,
) -> None:
    events: list[RuntimeEvent] = []
    manager = RuntimeManager(tmp_path, CancelledExecutor(), on_event=events.append)

    with pytest.raises(ProcessingCancelled, match="cancel"):
        manager.install(_model_plan(tmp_path))

    status = manager.status("mineru")
    assert status.state == "cancelled"
    assert status.failed_step == "models:download"
    assert events[-1].state == "cancelled"
    assert events[-1].phase == "models"
    assert events[-1].step == "download"


class TimeoutRecorder:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def run(
        self,
        argv: tuple[str, ...],
        env: dict[str, str] | None = None,
        *,
        timeout_seconds: float,
    ) -> None:
        self.timeouts.append(timeout_seconds)


def test_manager_uses_step_deadline_and_supports_explicit_override(tmp_path: Path) -> None:
    executor = TimeoutRecorder()
    RuntimeManager(tmp_path, executor).install(_model_plan(tmp_path))
    assert executor.timeouts == [pytest.approx(12.5)]

    override = TimeoutRecorder()
    RuntimeManager(tmp_path, override, timeout_seconds=3.5).repair(
        _model_plan(tmp_path), "mineru"
    )
    assert override.timeouts == [pytest.approx(3.5)]


def test_step_timeout_is_overall_across_retries(tmp_path: Path) -> None:
    class SlowFailureExecutor:
        def __init__(self) -> None:
            self.calls = 0

        def run(
            self,
            argv: tuple[str, ...],
            env: dict[str, str] | None = None,
            *,
            timeout_seconds: float,
        ) -> None:
            self.calls += 1
            time.sleep(0.06)
            raise RuntimeError("temporary failure")

    executor = SlowFailureExecutor()
    backend = RuntimeBackendPlan(
        name="mineru",
        environment_dir=tmp_path / "envs" / "mineru",
        acceleration="cpu",
        steps=(
            InstallStep(
                "download",
                ("tool", "download"),
                attempts=3,
                timeout_seconds=0.1,
            ),
        ),
    )
    manager = RuntimeManager(tmp_path, executor)

    with pytest.raises(RuntimeInstallError, match="overall") as caught:
        manager.repair(RuntimePlan(tmp_path, (backend,)), "mineru")

    assert isinstance(caught.value.__cause__, RuntimeCommandTimeoutError)
    assert executor.calls < 3, "each retry received a fresh timeout budget"
