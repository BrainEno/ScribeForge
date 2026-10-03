from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class HardwareProfile:
    os_name: str
    architecture: str
    gpu_name: str | None
    nvidia_driver: str | None
    vram_mb: int | None
    ram_mb: int | None


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    root: Path

    def environment_dir(self, backend: str) -> Path:
        return self.root / "envs" / backend

    def python_executable(self, backend: str, os_name: str) -> Path:
        return self.executable(backend, "python", os_name)

    def executable(self, backend: str, name: str, os_name: str) -> Path:
        environment = self.environment_dir(backend)
        if os_name == "windows":
            suffix = ".exe" if not name.endswith(".exe") else ""
            return environment / "Scripts" / f"{name}{suffix}"
        return environment / "bin" / name

    @property
    def models_dir(self) -> Path:
        return self.root / "models"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def tools_dir(self) -> Path:
        return self.root / "tools"


@dataclass(frozen=True, slots=True)
class InstallStep:
    name: str
    argv: tuple[str, ...]
    attempts: int = 1

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")


@dataclass(frozen=True, slots=True)
class RuntimeBackendPlan:
    name: str
    environment_dir: Path
    acceleration: str
    steps: tuple[InstallStep, ...]
    model_steps: tuple[InstallStep, ...] = ()
    health_checks: tuple[InstallStep, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimePlan:
    root: Path
    backends: tuple[RuntimeBackendPlan, ...]

    def backend(self, name: str) -> RuntimeBackendPlan:
        for backend in self.backends:
            if backend.name == name:
                return backend
        raise KeyError(name)
