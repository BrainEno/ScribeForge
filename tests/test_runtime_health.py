from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scribeforge.cli import run
from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    cancellation_scope,
)
from scribeforge.runtime.health import check_runtime
from scribeforge.runtime.models import HardwareProfile, RuntimeLayout


def _profile() -> HardwareProfile:
    return HardwareProfile("linux", "x86_64", None, None, None, None)


def _installed(root: Path, names: tuple[str, ...] = ("mineru", "paddleocr")) -> None:
    layout = RuntimeLayout(root)
    for name in names:
        python = layout.python_executable(name, "linux")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("fixture", encoding="utf-8")
    (root / "runtime-state.json").write_text(
        json.dumps({"schema_version": 1, "backends": {name: {"state": "ready"} for name in names}}),
        encoding="utf-8",
    )


class _Executor:
    def __init__(self, failure: str | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[tuple[str, ...], dict[str, str] | None, float]] = []

    def run(
        self, argv: tuple[str, ...], env: dict[str, str] | None = None, *, timeout_seconds: float
    ) -> None:
        self.calls.append((argv, env, timeout_seconds))
        if argv[-1] == self.failure:
            raise subprocess.CalledProcessError(1, argv, stderr="test diagnostic")


def test_synthetic_checks_do_not_install_or_modify_runtime_state(tmp_path: Path) -> None:
    _installed(tmp_path)
    state = (tmp_path / "runtime-state.json").read_bytes()
    executor = _Executor()
    progress: list[str] = []
    results = check_runtime(
        tmp_path, executor=executor, hardware_probe=_profile, on_start=progress.append
    )
    assert [result.state for result in results] == ["passed", "passed"]
    assert progress == ["mineru", "paddleocr"]
    assert (tmp_path / "runtime-state.json").read_bytes() == state
    assert [call[0][-1] for call in executor.calls] == ["mineru", "paddleocr"]
    for argv, env, timeout in executor.calls:
        assert argv[1:3] == ("-m", "scribeforge.workers.runtime_ocr_smoke")
        assert env is not None and env["HF_HUB_OFFLINE"] == "1"
        assert env["TRANSFORMERS_OFFLINE"] == "1"
        assert timeout == 600


def test_one_failure_does_not_hide_other_engine(tmp_path: Path) -> None:
    _installed(tmp_path)
    executor = _Executor("mineru")
    results = check_runtime(tmp_path, executor=executor, hardware_probe=_profile)
    assert [(item.backend, item.state, item.detail) for item in results] == [
        ("mineru", "failed", "test diagnostic"),
        ("paddleocr", "passed", None),
    ]
    assert len(executor.calls) == 2


def test_missing_interpreter_and_state_are_not_tested(tmp_path: Path) -> None:
    _installed(tmp_path, ("mineru",))
    RuntimeLayout(tmp_path).python_executable("mineru", "linux").unlink()
    executor = _Executor()
    results = check_runtime(tmp_path, executor=executor, hardware_probe=_profile)
    assert [item.state for item in results] == ["not_ready", "not_ready"]
    assert "missing" in (results[0].detail or "")
    assert executor.calls == []


def test_cancel_does_not_claim_success(tmp_path: Path) -> None:
    _installed(tmp_path)
    token = CancellationToken()
    executor = _Executor()
    with cancellation_scope(token), pytest.raises(ProcessingCancelled):
        check_runtime(
            tmp_path, executor=executor, hardware_probe=_profile, on_start=lambda _: token.cancel()
        )
    assert executor.calls == []


def test_cli_emits_progress_and_partial_result_without_creating_runtime(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    output = io.StringIO()
    assert run(["runtime-check", "--runtime-root", str(root)], stdout=output) == 1
    events: list[dict[str, Any]] = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [event["type"] for event in events] == ["progress", "progress", "result"]
    assert [result["state"] for result in events[-1]["results"]] == ["not_ready", "not_ready"]
    assert events[-1]["success"] is False
    assert not root.exists()


def test_cli_handles_cancellation_and_unexpected_check_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scribeforge.runtime import health

    def cancelled(*_args: object, **_kwargs: object) -> object:
        raise ProcessingCancelled()

    monkeypatch.setattr(health, "check_runtime", cancelled)
    output = io.StringIO()
    assert run(["runtime-check", "--runtime-root", str(tmp_path)], stdout=output) == 130
    assert json.loads(output.getvalue())["cancelled"] is True

    def broken(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("diagnostic failure")

    monkeypatch.setattr(health, "check_runtime", broken)
    output = io.StringIO()
    assert run(["runtime-check", "--runtime-root", str(tmp_path)], stdout=output) == 2
    event = json.loads(output.getvalue())
    assert event["success"] is False and event["error"] == "diagnostic failure"


def test_failure_detail_is_bounded_and_best_effort_redacted(tmp_path: Path) -> None:
    _installed(tmp_path)

    class Failing:
        def run(
            self,
            argv: tuple[str, ...],
            env: dict[str, str] | None = None,
            *,
            timeout_seconds: float,
        ) -> None:
            raise RuntimeError(f"{Path.home()}/private " + "x" * 5000)

    results = check_runtime(tmp_path, executor=Failing(), hardware_probe=_profile)
    assert all(result.state == "failed" for result in results)
    assert all(len(result.detail or "") <= 4000 for result in results)
    assert all(str(Path.home()) not in (result.detail or "") for result in results)
