from __future__ import annotations

import io
import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from queue import Queue
from threading import Thread
from types import SimpleNamespace

import pytest

from scribeforge.cli import EXIT_CANCELLED, build_parser, run
from scribeforge.domain.cancellation import current_cancellation_token
from scribeforge.runtime.manager import RuntimeStatus
from scribeforge.runtime.workflow import RuntimeSetupWorkflow


class QueueInput:
    def __init__(self) -> None:
        self.lines: Queue[str] = Queue()

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        return self.lines.get()


def test_setup_parser_accepts_cancellation_and_step_timeout() -> None:
    args = build_parser().parse_args(
        ["setup", "--json", "--cancelable", "--step-timeout", "12.5"]
    )

    assert args.cancelable is True
    assert args.step_timeout == pytest.approx(12.5)
    with pytest.raises(SystemExit):
        build_parser().parse_args(["setup", "--json", "--step-timeout", "0"])


def test_stdin_cancellation_stops_setup_and_returns_machine_readable_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = tmp_path / "setup-started"
    control_input = QueueInput()
    monkeypatch.setattr(sys, "stdin", control_input)
    observed_timeouts: list[float | None] = []

    class BlockingWorkflow:
        def __init__(self, root: Path, *, timeout_seconds: float | None = None) -> None:
            observed_timeouts.append(timeout_seconds)

        def install(self, *, on_event) -> None:
            started.write_text("started", encoding="utf-8")
            token = current_cancellation_token()
            assert token is not None
            deadline = time.monotonic() + 5
            while not token.cancelled and time.monotonic() < deadline:
                time.sleep(0.01)
            token.raise_if_cancelled()

        def inspect(self) -> SimpleNamespace:
            return SimpleNamespace(
                mineru=RuntimeStatus("cancelled", "models:download"),
                paddleocr=RuntimeStatus("missing"),
            )

    monkeypatch.setattr(RuntimeSetupWorkflow, "__init__", BlockingWorkflow.__init__)
    monkeypatch.setattr(RuntimeSetupWorkflow, "install", BlockingWorkflow.install)
    monkeypatch.setattr(RuntimeSetupWorkflow, "inspect", BlockingWorkflow.inspect)

    output = io.StringIO()
    results: list[int] = []
    failures: list[Exception] = []

    def invoke() -> None:
        try:
            results.append(
                run(
                    [
                        "setup",
                        "--runtime-root",
                        str(tmp_path / "runtime"),
                        "--json",
                        "--cancelable",
                        "--step-timeout",
                        "12.5",
                    ],
                    stdout=output,
                )
            )
        except Exception as exc:  # noqa: BLE001 - rethrow thread failures in test
            failures.append(exc)

    thread = Thread(target=invoke)
    thread.start()
    deadline = time.monotonic() + 5
    while not started.exists() and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started.exists(), "the fake setup workflow did not start"
    control_input.lines.put('{"schema_version":1,"type":"cancel"}\n')
    thread.join(timeout=6)

    assert not thread.is_alive(), "setup did not stop after its cancellation message"
    assert failures == []
    assert results == [EXIT_CANCELLED]
    assert observed_timeouts == [12.5]
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert records[-1]["type"] == "result"
    assert records[-1]["success"] is False
    assert "cancel" in records[-1]["message"].lower()
    assert records[-1]["backends"]["mineru"] == {
        "state": "cancelled",
        "failed_step": "models:download",
    }
