from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scribeforge.runtime.models import RuntimePlan


class CommandExecutor(Protocol):
    def run(self, argv: tuple[str, ...]) -> None: ...


class SubprocessExecutor:
    def run(self, argv: tuple[str, ...]) -> None:
        subprocess.run(argv, check=True)


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    state: str
    failed_step: str | None = None


class RuntimeInstallError(RuntimeError):
    pass


class RuntimeManager:
    def __init__(self, root: Path, executor: CommandExecutor | None = None) -> None:
        self._root = root
        self._executor = executor or SubprocessExecutor()
        self._state_path = root / "runtime-state.json"

    def install(self, plan: RuntimePlan) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        for backend in plan.backends:
            backend.environment_dir.parent.mkdir(parents=True, exist_ok=True)
            self._set_status(backend.name, RuntimeStatus("installing"))
            for step in backend.steps:
                try:
                    self._executor.run(step.argv)
                except Exception as exc:
                    self._set_status(backend.name, RuntimeStatus("failed", step.name))
                    raise RuntimeInstallError(f"{backend.name}:{step.name}") from exc
            self._set_status(backend.name, RuntimeStatus("ready"))

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
