from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_real_uv_hash_install_and_console_script_survive_relocation(tmp_path: Path) -> None:
    uv = Path(sys.executable).with_name("uv.exe" if os.name == "nt" else "uv")
    assert uv.is_file(), "test dependencies must provide uv 0.13.7"
    lock = Path(__file__).parent / "fixtures" / "wheel-hash-lock.txt"
    staging = tmp_path / "staging"
    final = tmp_path / "final"
    python_name = "python.exe" if os.name == "nt" else "python"
    scripts_dir = "Scripts" if os.name == "nt" else "bin"
    environment = os.environ.copy()
    environment["UV_CACHE_DIR"] = str(tmp_path / "cache")
    environment["UV_PYTHON_INSTALL_DIR"] = str(tmp_path / "python")

    subprocess.run(
        [uv, "venv", "--relocatable", "--python", sys.executable, staging],
        check=True,
        env=environment,
        timeout=60,
    )
    staging_python = staging / scripts_dir / python_name
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            staging_python,
            "--require-hashes",
            "--no-deps",
            "-r",
            lock,
        ],
        check=True,
        env=environment,
        timeout=120,
    )

    staging.replace(final)
    wheel = final / scripts_dir / ("wheel.exe" if os.name == "nt" else "wheel")
    result = subprocess.run(
        [wheel, "version"],
        check=True,
        env=environment,
        timeout=30,
        capture_output=True,
        text=True,
    )

    assert "wheel 0.45.1" in result.stdout
    if os.name != "nt":
        assert str(staging) not in wheel.read_text(encoding="utf-8")
