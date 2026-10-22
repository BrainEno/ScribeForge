from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from pathlib import Path


def default_runtime_root(
    *,
    home: Path | None = None,
    environment: Mapping[str, str] | None = None,
    os_name: str | None = None,
) -> Path:
    """Return the per-user runtime directory shared by setup and book projects."""
    user_home = Path.home() if home is None else home
    env = os.environ if environment is None else environment
    system = (platform.system() if os_name is None else os_name).lower()

    if system == "windows":
        local_app_data = env.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else user_home / "AppData" / "Local"
    elif system in {"darwin", "macos"}:
        base = user_home / "Library" / "Application Support"
    else:
        data_home = env.get("XDG_DATA_HOME")
        base = Path(data_home) if data_home and Path(data_home).is_absolute() else user_home / ".local" / "share"

    return base / "ScribeForge" / "runtime"
