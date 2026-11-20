"""Validation and ownership for desktop runtime locations.

A runtime directory has one model binding, independent of whichever UI profile
is active. This prevents a failed candidate profile from changing the status
or model selection of a working environment in the same directory.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from scribeforge.runtime import sizes
from scribeforge.runtime.models import RuntimeLayout
from scribeforge.workers.paddle_model_setup import PADDLE_MODELS

BINDING_FILENAME = "model-location.json"
STATE_FILENAME = "runtime-state.json"
LOCK_FILENAME = ".runtime-location.lock"
CORE_BACKENDS = ("mineru", "paddleocr")
_MODELS: ContextVar[Path | None] = ContextVar("external_models", default=None)


@contextmanager
def model_location_scope(path: Path | None) -> Iterator[None]:
    token = _MODELS.set(path)
    try:
        yield
    finally:
        _MODELS.reset(token)


def external_model_location() -> Path | None:
    return _MODELS.get()


class LocationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise LocationError("relative_path", f"{label} must be an absolute directory path.")
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise LocationError("invalid_path", f"Cannot resolve {label}: {exc}") from exc
    if resolved == resolved.parent:
        raise LocationError(
            "drive_root", f"Choose a subdirectory instead of a drive root for {label}."
        )
    return resolved


def _ancestor(path: Path) -> Path:
    for candidate in (path, *path.parents):
        if candidate.exists():
            if not candidate.is_dir():
                raise LocationError("not_directory", f"Not a directory: {candidate}")
            return candidate
    raise LocationError("unavailable_drive", f"The drive is not available: {path}")


def _same_or_nested(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _read_binding(path: Path) -> Path | None:
    try:
        with path.open(encoding="utf-8") as file:
            data = json.loads(file.read(16384))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("unsupported binding schema")
        models = data["models"]
        if models is None:
            return None
        if not isinstance(models, str):
            raise TypeError("invalid model path")
        return _canonical(Path(models), "saved model location")
    except (OSError, ValueError) as exc:
        raise LocationError(
            "binding_unreadable", f"Cannot read model binding {path}: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class RuntimeLocation:
    root: Path
    models: Path | None

    @classmethod
    def resolve(cls, root: Path, models: Path | None) -> RuntimeLocation:
        root = _canonical(root, "runtime location")
        models = _canonical(models, "model location") if models is not None else None
        if models is not None and _same_or_nested(root, models):
            raise LocationError(
                "overlapping_paths",
                "Reuse models from a separate directory. For models inside this runtime, "
                "leave the model override blank.",
            )
        return cls(root, models)

    @property
    def managed_models(self) -> Path:
        return self.root / "models"

    @property
    def model_path(self) -> Path:
        return self.models if self.models is not None else self.managed_models

    @property
    def binding_file(self) -> Path:
        return self.root / BINDING_FILENAME

    def check_binding(self) -> None:
        if self.binding_file.exists() or self.binding_file.is_symlink():
            if self.binding_file.is_symlink() or _read_binding(self.binding_file) != self.models:
                raise LocationError(
                    "binding_mismatch",
                    "This runtime belongs to a different model location. Select the original "
                    "model location or prepare a new runtime directory.",
                )
        elif self.models is not None and (
            (self.root / STATE_FILENAME).exists()
            or any((self.root / name).exists() for name in ("envs", "models"))
        ):
            raise LocationError(
                "already_managed",
                "This runtime already uses managed models. Prepare a new runtime directory "
                "to reuse external models without disrupting the existing runtime.",
            )

    def check_paths(self) -> list[Path]:
        _ancestor(self.root)
        if any(
            (ancestor / "project.sqlite3").is_file() for ancestor in (self.root, *self.root.parents)
        ):
            raise LocationError(
                "project_directory", "Do not install the runtime inside a book project."
            )
        if self.root.is_dir():
            managed_names = {
                "cache",
                "envs",
                "models",
                "tools",
                STATE_FILENAME,
                BINDING_FILENAME,
                "runtime-state.tmp",
                LOCK_FILENAME,
            }
            for child in self.root.iterdir():
                if child.is_symlink() and child.name in managed_names:
                    raise LocationError(
                        "linked_runtime", f"Managed runtime entry is a symbolic link: {child}"
                    )
                if child.name not in managed_names and not child.name.startswith(
                    ("runtime-state.json.", ".model-location-", ".scribeforge-location-check-")
                ):
                    raise LocationError(
                        "not_dedicated",
                        f"Choose an empty folder or a dedicated ScribeForge runtime: {child}",
                    )
        if any(
            (parent / STATE_FILENAME).exists() or (parent / BINDING_FILENAME).exists()
            for parent in self.root.parents
        ):
            raise LocationError("nested_runtime", "Choose a folder outside existing runtimes.")
        self.check_binding()
        if self.models is None:
            return []
        if not self.models.is_dir():
            raise LocationError(
                "missing_models", f"Existing model directory is not available: {self.models}"
            )
        required = [self.models / "mineru"] + [
            self.models / "paddleocr" / "verified" / model.name for model in PADDLE_MODELS
        ]
        for path in required:
            if not path.is_dir() or not any(path.iterdir()):
                raise LocationError(
                    "missing_model_files", f"Expected a non-empty model directory: {path}"
                )
        return required

    @classmethod
    def from_bound_root(cls, root: Path) -> RuntimeLocation | None:
        binding = root / BINDING_FILENAME
        if not binding.exists() and not binding.is_symlink():
            return None
        if binding.is_symlink():
            raise LocationError(
                "linked_runtime", f"The model binding is a symbolic link: {binding}"
            )
        return cls.resolve(root, _read_binding(binding))

    @contextmanager
    def configuration_lock(self) -> Iterator[None]:
        """Serialize explicit setup/cleanup; a killed process releases the OS lock."""
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / LOCK_FILENAME
        if lock.is_symlink():
            raise LocationError("linked_runtime", f"The runtime lock is a symbolic link: {lock}")
        with lock.open("a+b") as file:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    file.seek(0)
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise LocationError(
                    "runtime_in_use", "Another process is configuring this runtime."
                ) from exc
            # Closing the file releases the lock, including on exceptions.
            yield

    def claim(self) -> None:
        """Publish an immutable binding while holding configuration_lock."""
        self.check_paths()
        if self.binding_file.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        contents = json.dumps(
            {"schema_version": 1, "models": str(self.models) if self.models is not None else None},
            ensure_ascii=True,
        )
        # Write before publishing so another process never reads a partial binding.
        with NamedTemporaryFile(dir=self.root, prefix=".model-location-", delete=False) as file:
            temporary = Path(file.name)
            try:
                file.write(contents.encode("ascii"))
                file.flush()
                os.fsync(file.fileno())
            except BaseException:
                file.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            # The caller holds configuration_lock for the entire setup.
            temporary.replace(self.binding_file)
        finally:
            temporary.unlink(missing_ok=True)


def location_report(root: Path, models: Path | None, os_name: str) -> dict[str, object]:
    location = RuntimeLocation.resolve(root, models)
    required_paths = location.check_paths()
    existing = _ancestor(location.root)
    # This bounded create/remove detects ACL and read-only volume errors that
    # os.access cannot reliably identify, particularly on Windows.
    try:
        with NamedTemporaryFile(dir=existing, prefix=".scribeforge-location-check-") as probe:
            probe.write(b"ScribeForge")
            probe.flush()
    except OSError as exc:
        raise LocationError("not_writable", f"Cannot write to {existing}: {exc}") from exc
    from scribeforge.runtime.manager import RuntimeManager

    manager = RuntimeManager(location.root)
    layout = RuntimeLayout(location.root)
    backends = {
        name: manager.status(name).state
        if layout.python_executable(name, os_name).is_file()
        else "missing"
        for name in CORE_BACKENDS
    }
    pending = [name for name, state in backends.items() if state != "ready"]
    budget = sizes.preflight(location.root, pending, reuse_models=location.models is not None)
    return {
        "schema_version": 1,
        "ok": True,
        "runtime_root": str(location.root),
        "models_root": str(location.model_path),
        "external_models": location.models is not None,
        "checked_model_directories": [str(path) for path in required_paths],
        "backends": backends,
        "disk": budget,
    }
