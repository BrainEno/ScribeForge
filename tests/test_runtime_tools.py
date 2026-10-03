from pathlib import Path

import pytest

from scribeforge.runtime.tools import RuntimeToolMissing, resolve_uv_executable


def test_release_prefers_bundled_uv_over_system_path(tmp_path: Path) -> None:
    bundled = tmp_path / "uv.exe"
    bundled.write_bytes(b"binary")

    resolved = resolve_uv_executable(tmp_path, "windows", lambda _: "C:/system/uv.exe")

    assert resolved == str(bundled)


def test_development_can_fall_back_to_uv_on_path(tmp_path: Path) -> None:
    resolved = resolve_uv_executable(tmp_path, "linux", lambda _: "/usr/bin/uv")

    assert resolved == "/usr/bin/uv"


def test_missing_uv_has_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(RuntimeToolMissing, match="uv bootstrap tool"):
        resolve_uv_executable(tmp_path, "windows", lambda _: None)
