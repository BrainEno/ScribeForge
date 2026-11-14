from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from scribeforge.runtime.manager import (
    CommandExecutor,
    RuntimeEvent,
    RuntimeManager,
    RuntimeStatus,
)
from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan
from scribeforge.runtime.probe import probe_hardware
from scribeforge.runtime.tools import (
    RuntimeToolMissing,
    ensure_uv_executable,
    resolve_uv_executable,
)


@dataclass(frozen=True, slots=True)
class RuntimeOverview:
    hardware: HardwareProfile
    uv_available: bool
    mineru: RuntimeStatus
    paddleocr: RuntimeStatus
    #: Health of the optional technology-route backends (issue #90), keyed by
    #: backend name. Absent in older callers; consumers use ``getattr``.
    optional_backends: dict[str, RuntimeStatus] = field(default_factory=dict)


#: Optional technology-route backends (issue #90) reported by name in status.
OPTIONAL_ROUTE_BACKENDS: tuple[str, ...] = ("paddleocr-vl", "surya")

HardwareProbe = Callable[[], HardwareProfile]
UvEnsurer = Callable[[Path, str, str], str]
UvResolver = Callable[[Path, str], str]


class RuntimeSetupWorkflow:
    """Application-facing orchestration over the existing managed runtime manager."""

    def __init__(
        self,
        root: Path,
        *,
        hardware_probe: HardwareProbe = probe_hardware,
        uv_ensurer: UvEnsurer = ensure_uv_executable,
        uv_resolver: UvResolver = resolve_uv_executable,
        executor: CommandExecutor | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.root = root
        self._hardware_probe = hardware_probe
        self._uv_ensurer = uv_ensurer
        self._uv_resolver = uv_resolver
        self._executor = executor
        self._timeout_seconds = timeout_seconds

    def inspect(self) -> RuntimeOverview:
        profile = self._hardware_probe()
        layout = RuntimeLayout(self.root)
        try:
            self._uv_resolver(layout.tools_dir, profile.os_name)
        except RuntimeToolMissing:
            uv_available = False
        else:
            uv_available = True
        manager = RuntimeManager(self.root, executor=self._executor)
        return RuntimeOverview(
            hardware=profile,
            uv_available=uv_available,
            mineru=manager.status("mineru"),
            paddleocr=manager.status("paddleocr"),
            optional_backends={name: manager.status(name) for name in OPTIONAL_ROUTE_BACKENDS},
        )

    def install(self, *, on_event: Callable[[RuntimeEvent], None] | None = None) -> None:
        profile = self._hardware_probe()
        layout = RuntimeLayout(self.root)
        uv = self._uv_ensurer(layout.tools_dir, profile.os_name, profile.architecture)
        plan = build_runtime_plan(profile, layout, uv)
        RuntimeManager(
            self.root,
            executor=self._executor,
            on_event=on_event,
            timeout_seconds=self._timeout_seconds,
        ).install(plan)

    def repair(
        self,
        backend: str,
        *,
        on_event: Callable[[RuntimeEvent], None] | None = None,
    ) -> None:
        profile = self._hardware_probe()
        layout = RuntimeLayout(self.root)
        uv = self._uv_ensurer(layout.tools_dir, profile.os_name, profile.architecture)
        plan = build_runtime_plan(profile, layout, uv)
        RuntimeManager(
            self.root,
            executor=self._executor,
            on_event=on_event,
            timeout_seconds=self._timeout_seconds,
        ).repair(plan, backend)
