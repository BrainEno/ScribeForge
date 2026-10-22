from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scribeforge.runtime.manager import RuntimeEvent, RuntimeManager, RuntimeStatus
from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan
from scribeforge.runtime.probe import probe_hardware
from scribeforge.runtime.tools import RuntimeToolMissing, ensure_uv_executable, resolve_uv_executable


@dataclass(frozen=True, slots=True)
class RuntimeOverview:
    hardware: HardwareProfile
    uv_available: bool
    mineru: RuntimeStatus
    paddleocr: RuntimeStatus


HardwareProbe = Callable[[], HardwareProfile]
UvEnsurer = Callable[[Path, str, str], str]


class RuntimeSetupWorkflow:
    """Application-facing orchestration over the existing managed runtime manager."""

    def __init__(
        self,
        root: Path,
        *,
        hardware_probe: HardwareProbe = probe_hardware,
        uv_ensurer: UvEnsurer = ensure_uv_executable,
    ) -> None:
        self.root = root
        self._hardware_probe = hardware_probe
        self._uv_ensurer = uv_ensurer

    def inspect(self) -> RuntimeOverview:
        profile = self._hardware_probe()
        layout = RuntimeLayout(self.root)
        try:
            resolve_uv_executable(layout.tools_dir, profile.os_name)
        except RuntimeToolMissing:
            uv_available = False
        else:
            uv_available = True
        manager = RuntimeManager(self.root)
        return RuntimeOverview(
            hardware=profile,
            uv_available=uv_available,
            mineru=manager.status("mineru"),
            paddleocr=manager.status("paddleocr"),
        )

    def install(
        self, *, on_event: Callable[[RuntimeEvent], None] | None = None
    ) -> None:
        profile = self._hardware_probe()
        layout = RuntimeLayout(self.root)
        uv = self._uv_ensurer(layout.tools_dir, profile.os_name, profile.architecture)
        plan = build_runtime_plan(profile, layout, uv)
        RuntimeManager(self.root, on_event=on_event).install(plan)

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
        RuntimeManager(self.root, on_event=on_event).repair(plan, backend)
