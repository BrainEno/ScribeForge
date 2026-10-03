from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path


class RuntimeToolMissing(RuntimeError):
    pass


def resolve_uv_executable(
    tools_dir: Path,
    os_name: str,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    executable_name = "uv.exe" if os_name == "windows" else "uv"
    bundled = tools_dir / executable_name
    if bundled.is_file():
        return str(bundled)
    system = which("uv")
    if system:
        return system
    raise RuntimeToolMissing(
        "uv bootstrap tool is unavailable; packaged ScribeForge builds must bundle uv"
    )
