from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from scribeforge.adapters.subprocess_ocr import (
    DEFAULT_WORKER_TIMEOUT_SECONDS,
    SubprocessOCREngine,
)
from scribeforge.runtime.environment import build_runtime_environment
from scribeforge.runtime.models import RuntimeLayout


class BackendNotReadyError(RuntimeError):
    """Raised when a worker backend is not installed or healthy enough to run."""


def worker_source_root() -> Path:
    """Locate Python sources readable by the separate managed OCR interpreter.

    PyInstaller's module archive is private to the frozen executable. A child
    Python process cannot import from it, so desktop distributions carry the
    small Worker package as ordinary source files beside the frozen backend.
    Development checkouts continue to use ``src`` without generating files.
    """

    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", None)
        candidates = []
        if isinstance(bundle, str) and bundle:
            candidates.append(Path(bundle) / "worker-src")
        candidates.append(Path(sys.executable).resolve().parent / "worker-src")
        for candidate in candidates:
            if (candidate / "scribeforge" / "workers" / "protocol.py").is_file():
                return candidate
        searched = ", ".join(str(candidate) for candidate in candidates)
        raise BackendNotReadyError(
            "the desktop backend worker source bundle is missing; "
            f"reinstall the complete desktop package (searched: {searched})"
        )
    return Path(__file__).resolve().parents[2]


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
    """Centralize model/cache directories and worker imports.

    Workers run inside a managed backend environment that does not install the
    application itself, so the project sources must be on ``PYTHONPATH``.
    """

    environment = build_runtime_environment(layout)
    existing = os.environ.get("PYTHONPATH")
    sources = str(worker_source_root())
    environment["PYTHONPATH"] = f"{sources}{os.pathsep}{existing}" if existing else sources
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
        raise BackendNotReadyError(f"backend {backend} python executable is missing: {python}")


def build_runtime_engine(
    layout: RuntimeLayout,
    backend: str,
    worker_script: Path,
    os_name: str,
    *,
    name: str,
    status: Callable[[str], str],
    extra_environment: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_WORKER_TIMEOUT_SECONDS,
) -> SubprocessOCREngine:
    """Build an OCR engine bound to an application-managed backend runtime.

    The backend must already be installed and healthy at construction time, and
    the same check re-runs before every page so a runtime removed mid-book fails
    loudly rather than silently producing wrong evidence.
    """

    def preflight() -> None:
        require_backend_ready(layout, backend, os_name, status)

    preflight()
    python = layout.python_executable(backend, os_name)
    base_path = os.environ.get("PATH", "")
    extra = dict(extra_environment or {})
    extra["PATH"] = f"{python.parent}{os.pathsep}{base_path}" if base_path else str(python.parent)
    return SubprocessOCREngine(
        name,
        worker_command(layout, backend, worker_script, os_name),
        environment=runtime_environment(layout, extra),
        preflight=preflight,
        timeout_seconds=timeout_seconds,
    )
