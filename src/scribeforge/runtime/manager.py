from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scribeforge.runtime.models import InstallStep, RuntimeBackendPlan, RuntimePlan


class CommandExecutor(Protocol):
    def run(self, argv: tuple[str, ...], env: dict[str, str] | None = None) -> None: ...


class SubprocessExecutor:
    def run(self, argv: tuple[str, ...], env: dict[str, str] | None = None) -> None:
        process_env = None
        if env:
            process_env = os.environ.copy()
            process_env.update(env)
        subprocess.run(
            argv,
            check=True,
            env=process_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    state: str
    failed_step: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    backend: str
    phase: str
    state: str
    step: str | None
    completed_steps: int
    total_steps: int


class RuntimeInstallError(RuntimeError):
    pass


class RuntimeManager:
    def __init__(
        self,
        root: Path,
        executor: CommandExecutor | None = None,
        on_event: Callable[[RuntimeEvent], None] | None = None,
    ) -> None:
        self._root = root
        self._executor = executor or SubprocessExecutor()
        self._on_event = on_event
        self._state_path = root / "runtime-state.json"

    def install(self, plan: RuntimePlan) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        failures: list[str] = []
        for backend in plan.backends:
            if backend.optional:
                continue
            try:
                self._run_backend(backend)
            except RuntimeInstallError as exc:
                # One broken backend should not prevent the other OCR engine
                # from being installed and usable.
                failures.append(str(exc))
        if failures:
            raise RuntimeInstallError("; ".join(failures))

    def repair(self, plan: RuntimePlan, backend: str) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        self._run_backend(plan.backend(backend))

    def status(self, backend: str) -> RuntimeStatus:
        state = self._read_state()
        backends = state.get("backends")
        if not isinstance(backends, dict):
            return RuntimeStatus("missing")
        raw = backends.get(backend)
        if not isinstance(raw, dict):
            return RuntimeStatus("missing")
        value = raw.get("state")
        failed_step = raw.get("failed_step")
        return RuntimeStatus(
            state=value if isinstance(value, str) else "missing",
            failed_step=failed_step if isinstance(failed_step, str) else None,
        )

    def _run_backend(self, backend: RuntimeBackendPlan) -> None:
        backend.environment_dir.parent.mkdir(parents=True, exist_ok=True)
        environment = dict(backend.environment)
        phases = (
            ("install", backend.steps),
            ("models", backend.model_steps),
            ("health", backend.health_checks),
        )
        total = sum(len(steps) for _, steps in phases)
        completed = 0
        self._set_status(backend.name, RuntimeStatus("installing"))

        for phase, steps in phases:
            if not steps:
                continue
            phase_state = "installing" if phase == "install" else phase
            self._set_status(backend.name, RuntimeStatus(phase_state))
            self._emit(RuntimeEvent(backend.name, phase, "started", None, completed, total))
            for step in steps:
                try:
                    self._run_step(step, environment)
                except Exception as exc:
                    failed_step = step.name if phase == "install" else f"{phase}:{step.name}"
                    self._set_status(backend.name, RuntimeStatus("failed", failed_step))
                    self._emit(
                        RuntimeEvent(
                            backend.name,
                            phase,
                            "failed",
                            step.name,
                            completed,
                            total,
                        )
                    )
                    detail = ""
                    if isinstance(exc, subprocess.CalledProcessError):
                        detail = (exc.stderr or exc.stdout or "").strip()
                    if not detail:
                        detail = str(exc).strip()
                    if len(detail) > 1200:
                        detail = detail[-1200:]
                    suffix = f": {detail}" if detail else ""
                    raise RuntimeInstallError(
                        f"{backend.name}:{failed_step}{suffix}"
                    ) from exc
                completed += 1
            self._emit(RuntimeEvent(backend.name, phase, "completed", None, completed, total))

        self._set_status(backend.name, RuntimeStatus("ready"))
        self._emit(RuntimeEvent(backend.name, "complete", "ready", None, completed, total))

    def _run_step(self, step: InstallStep, environment: dict[str, str]) -> None:
        for attempt in range(1, step.attempts + 1):
            try:
                if environment:
                    self._executor.run(step.argv, environment)
                else:
                    self._executor.run(step.argv)
                return
            except Exception:
                if attempt == step.attempts:
                    raise

    def _emit(self, event: RuntimeEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)

    def _set_status(self, backend: str, status: RuntimeStatus) -> None:
        state = self._read_state()
        backends = state.setdefault("backends", {})
        if not isinstance(backends, dict):
            backends = {}
            state["backends"] = backends
        backends[backend] = {
            "state": status.state,
            "failed_step": status.failed_step,
        }
        self._write_state(state)

    def _read_state(self) -> dict[str, object]:
        if not self._state_path.exists():
            return {"schema_version": 1, "backends": {}}
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 1, "backends": {}}
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            return {"schema_version": 1, "backends": {}}
        return raw

    def _write_state(self, state: dict[str, object]) -> None:
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self._state_path)
