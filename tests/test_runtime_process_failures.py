from __future__ import annotations

import signal
import subprocess
import sys
from typing import BinaryIO

import pytest

from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    cancellation_scope,
)
from scribeforge.runtime import process as runtime_process


def test_success_returns_stdout_and_bounded_stderr_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_process, "_MAX_ERROR_DETAIL_BYTES", 3)
    command = (
        sys.executable,
        "-c",
        "import sys;sys.stdout.write('ok');sys.stderr.write('abcdef')",
    )

    code, stdout, stderr = runtime_process.run_runtime_process(
        command,
        environment=None,
        timeout_seconds=2,
        max_output_bytes=32,
        max_log_bytes=32,
    )

    assert (code, stdout, stderr) == (0, "ok", "def")


def test_pre_cancelled_command_is_never_started(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("a cancelled command must not create a process")

    monkeypatch.setattr(runtime_process.subprocess, "Popen", unexpected)
    token = CancellationToken()
    token.cancel()

    with cancellation_scope(token), pytest.raises(ProcessingCancelled):
        runtime_process.run_runtime_process(
            ("tool",),
            environment=None,
            timeout_seconds=1,
            max_output_bytes=32,
            max_log_bytes=32,
        )


class _RunningProcess:
    def __init__(self) -> None:
        self.pid = 4321
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest.mark.parametrize(
    ("interrupt", "expected"),
    [(KeyboardInterrupt(), ProcessingCancelled), (RuntimeError("reader failed"), RuntimeError)],
    ids=["keyboard-interrupt", "reader-error"],
)
def test_monitoring_failures_always_terminate_process(
    monkeypatch: pytest.MonkeyPatch, interrupt: BaseException, expected: type[BaseException]
) -> None:
    child = _RunningProcess()
    cleanup: list[int] = []
    monkeypatch.setattr(runtime_process.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(
        runtime_process,
        "_terminate_process_tree",
        lambda process: cleanup.append(process.pid),
    )

    def fail_control(*args: object, **kwargs: object) -> None:
        raise interrupt

    monkeypatch.setattr(runtime_process, "_check_control", fail_control)
    with pytest.raises(expected):
        runtime_process.run_runtime_process(
            ("tool",),
            environment=None,
            timeout_seconds=1,
            max_output_bytes=32,
            max_log_bytes=32,
        )

    assert cleanup == [child.pid]


def test_windows_tree_cleanup_uses_taskkill_and_kills_survivor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _RunningProcess()
    taskkill: list[list[str]] = []
    monkeypatch.setattr(runtime_process.os, "name", "nt")
    monkeypatch.setattr(
        runtime_process.subprocess,
        "run",
        lambda argv, **kwargs: taskkill.append(argv),
    )

    runtime_process._terminate_process_tree(child)  # type: ignore[arg-type]

    assert taskkill == [["taskkill", "/PID", "4321", "/T", "/F"]]
    assert child.killed
    assert child.returncode == -9


def test_posix_tree_cleanup_falls_back_when_process_group_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _RunningProcess()
    original_wait = child.wait
    signals: list[int] = []

    def killpg(_pid: int, sig: int) -> None:
        signals.append(sig)
        if sig == signal.SIGTERM:
            raise OSError("process group unavailable")
        raise ProcessLookupError("already reaped")

    def wait(timeout: float | None = None) -> int:
        if timeout == 0.35:
            raise subprocess.TimeoutExpired("tool", timeout)
        return original_wait(timeout)

    monkeypatch.setattr(runtime_process.os, "name", "posix")
    monkeypatch.setattr(runtime_process.os, "killpg", killpg)
    monkeypatch.setattr(child, "wait", wait)

    runtime_process._terminate_process_tree(child)  # type: ignore[arg-type]

    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert child.terminated


def test_control_check_rejects_oversized_files(tmp_path, monkeypatch) -> None:
    stream: BinaryIO = (tmp_path / "output.bin").open("w+b")
    with stream:
        stream.write(b"12345")
        stream.flush()
        with pytest.raises(runtime_process.RuntimeCommandOutputLimitError, match="stdout"):
            runtime_process._check_control(
                None,
                stream,
                stream,
                max_output_bytes=4,
                max_log_bytes=10,
            )
        with pytest.raises(runtime_process.RuntimeCommandOutputLimitError, match="stderr"):
            runtime_process._check_control(
                None,
                stream,
                stream,
                max_output_bytes=10,
                max_log_bytes=4,
            )
