from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Sequence

from scribeforge.runtime.models import HardwareProfile


def parse_nvidia_smi(output: str) -> tuple[str, str, int] | None:
    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "")
    if not first_line:
        return None
    parts = [part.strip() for part in first_line.split(",")]
    if len(parts) != 3:
        return None
    try:
        vram_mb = int(float(parts[2]))
    except ValueError:
        return None
    return parts[0], parts[1], vram_mb


def _ram_mb() -> int | None:
    if os.name == "posix" and hasattr(os, "sysconf"):
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            pages = int(os.sysconf("SC_PHYS_PAGES"))
            return page_size * pages // (1024 * 1024)
        except (OSError, ValueError):
            return None
    if os.name == "nt":
        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[int64]((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB)",
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            return int(completed.stdout.strip())
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
    return None


def _nvidia_profile(command: Sequence[str] | None = None) -> tuple[str, str, int] | None:
    argv = list(command) if command is not None else [
        "nvidia-smi",
        "--query-gpu=name,driver_version,memory.total",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            argv,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_nvidia_smi(completed.stdout)


def probe_hardware() -> HardwareProfile:
    system = platform.system().lower()
    os_name = "windows" if system == "windows" else "macos" if system == "darwin" else system
    nvidia = _nvidia_profile()
    if nvidia is None:
        gpu_name = driver = None
        vram_mb = None
    else:
        gpu_name, driver, vram_mb = nvidia
    return HardwareProfile(
        os_name=os_name,
        architecture=platform.machine().lower(),
        gpu_name=gpu_name,
        nvidia_driver=driver,
        vram_mb=vram_mb,
        ram_mb=_ram_mb(),
    )
