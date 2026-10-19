from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from scribeforge.adapters.subprocess_ocr import SubprocessOCREngine
from scribeforge.runtime.environment import build_runtime_environment
from scribeforge.runtime.models import RuntimeLayout


class BackendNotReadyError(RuntimeError):
    """Raised when a worker backend is not installed or healthy enough to run."""


def worker_command(
    layout: RuntimeLayout,
    backend: str,
    worker_script: Path,
    os_name: str,
) -> tuple[str, ...]:
    """Resolve a worker command that runs inside the backend's managed Python."""

    python = layout.python_executable(backend, os_name)
    return (str(python), str(worker_script))


def runtime_environment(
    layout: RuntimeLayout,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Centralize model/cache directories for a worker backend runtime."""

    environment = build_runtime_environment(layout)
    if extra:
        environment.update(dict(extra))
    return environment


def require_backend_ready(
    layout: RuntimeLayout,
    backend: str,
    os_name: str,
    status: Callable[[str], str],
) -> None:
    """Fail loudly unless the backend is reported ready with an installed Python."""

    state = status(backend)
    if state != "ready":
        raise BackendNotReadyError(f"backend {backend} is not ready (state={state})")
    python = layout.python_executable(backend, os_name)
    if not python.exists():
        raise BackendNotReadyError(
            f"backend {backend} python executable is missing: {python}"
        )


def build_runtime_engine(
    layout: RuntimeLayout,
    backend: str,
    worker_script: Path,
    os_name: str,
    *,
    name: str,
    status: Callable[[str], str],
    extra_environment: Mapping[str, str] | None = None,
) -> SubprocessOCREngine:
    """Build an OCR engine bound to an application-managed backend runtime.

    The backend must already be installed and healthy at construction time, and
    the same check re-runs before every page so a runtime removed mid-book fails
    loudly rather than silently producing wrong evidence.
    """

    def preflight() -> None:
        require_backend_ready(layout, backend, os_name, status)

    preflight()
    return SubprocessOCREngine(
        name,
        worker_command(layout, backend, worker_script, os_name),
        environment=runtime_environment(layout, extra_environment),
        preflight=preflight,
    )
