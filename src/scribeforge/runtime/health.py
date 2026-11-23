"""Explicit, non-installing synthetic OCR checks for the active runtime.

This does not mark a runtime ready or modify project evidence. Third-party
libraries may write caches and temporary files. Offline flags are best effort,
not an operating-system network sandbox.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scribeforge.adapters.runtime_workers import runtime_environment
from scribeforge.domain.cancellation import ProcessingCancelled, raise_if_cancelled
from scribeforge.observability import redact_text
from scribeforge.runtime.manager import CommandExecutor, RuntimeManager, SubprocessExecutor
from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.probe import probe_hardware

BACKENDS = ("mineru", "paddleocr")
CHECK_TIMEOUT_SECONDS = 10 * 60.0
MAX_DETAIL = 4000


@dataclass(frozen=True, slots=True)
class HealthResult:
    backend: str
    state: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"backend": self.backend, "state": self.state, "detail": self.detail}


def _detail(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        text = error.stderr or error.stdout or str(error)
    else:
        text = str(error) or error.__class__.__name__
    return redact_text(str(text)).strip()[-MAX_DETAIL:]


def check_runtime(
    root: Path,
    *,
    executor: CommandExecutor | None = None,
    hardware_probe: Callable[[], HardwareProfile] = probe_hardware,
    on_start: Callable[[str], None] | None = None,
    timeout_seconds: float = CHECK_TIMEOUT_SECONDS,
) -> tuple[HealthResult, ...]:
    """Test both core engines, continuing after a failure, without installing.

    Only recorded-ready environments with an existing interpreter are run. The
    result is transient and must not be interpreted as a guarantee about later
    runs, the user's source pages, or recognition accuracy. Cancellation is
    propagated so callers cannot accidentally report a partial check as success.
    """
    if timeout_seconds <= 0:
        raise ValueError("health timeout must be positive")
    profile = hardware_probe()
    layout = RuntimeLayout(root)
    manager = RuntimeManager(root)
    runner = executor or SubprocessExecutor()
    results = []
    for backend in BACKENDS:
        raise_if_cancelled()
        if on_start is not None:
            on_start(backend)
        raise_if_cancelled()
        interpreter = layout.python_executable(backend, profile.os_name)
        if manager.status(backend).state != "ready":
            results.append(
                HealthResult(backend, "not_ready", "Recorded runtime state is not ready.")
            )
            continue
        if not interpreter.is_file():
            results.append(
                HealthResult(backend, "not_ready", "Managed Python executable is missing.")
            )
            continue
        environment = runtime_environment(
            layout,
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "MODELSCOPE_OFFLINE": "1",
                "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
            },
        )
        try:
            runner.run(
                (str(interpreter), "-m", "scribeforge.workers.runtime_ocr_smoke", backend),
                environment,
                timeout_seconds=timeout_seconds,
            )
        except ProcessingCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - report one engine without masking the other
            results.append(HealthResult(backend, "failed", _detail(exc)))
        else:
            results.append(HealthResult(backend, "passed"))
    return tuple(results)
