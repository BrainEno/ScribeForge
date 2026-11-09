from __future__ import annotations

import re
from pathlib import Path

import pytest

from scribeforge.runtime.models import HardwareProfile, RuntimeBackendPlan, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan


def _profile() -> HardwareProfile:
    return HardwareProfile("linux", "x86_64", None, None, None, 16384)


def _lock_step(backend: RuntimeBackendPlan):
    installs = [step for step in backend.steps if step.name == "install"]
    assert len(installs) == 1
    return installs[0]


def _lock_path(backend: RuntimeBackendPlan) -> Path:
    argv = _lock_step(backend).argv
    assert argv[1:4] == ("pip", "install", "--require-hashes")
    assert "--no-deps" in argv
    assert "--no-config" in argv
    assert "--default-index" in argv
    assert argv[argv.index("--default-index") + 1] == "https://pypi.org/simple"
    assert "-U" not in argv
    assert "-r" in argv
    return Path(argv[argv.index("-r") + 1])


def _locked_requirements(path: Path) -> list[str]:
    logical_lines: list[str] = []
    pending = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pending = f"{pending} {line}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].strip()
            continue
        logical_lines.append(pending)
        pending = ""
    assert not pending
    return logical_lines


@pytest.mark.parametrize("backend_name", ["mineru", "paddleocr", "paddleocr-vl", "surya"])
def test_every_backend_installs_only_from_a_complete_hash_lock(
    tmp_path: Path, backend_name: str
) -> None:
    plan = build_runtime_plan(_profile(), RuntimeLayout(tmp_path))
    backend = plan.backend(backend_name)

    lock = _lock_path(backend)

    assert lock.is_file(), lock
    assert "py312-universal" in lock.name
    entries = _locked_requirements(lock)
    packages = [entry for entry in entries if re.match(r"^[A-Za-z0-9_.-]+==", entry)]
    assert len(packages) > 5
    assert len(packages) == len(entries)
    for entry in packages:
        digests = re.findall(r"--hash=sha256:([0-9a-f]+)", entry)
        assert digests, entry
        assert all(len(digest) == 64 for digest in digests)
    roots = {
        "mineru": ("mineru==4.1.7",),
        "paddleocr": ("paddleocr==3.3.0", "paddlex==3.3.0"),
        "paddleocr-vl": ("paddleocr==3.3.0", "paddlex==3.3.0"),
        "surya": ("surya-ocr==0.14.0",),
    }
    for root in roots[backend_name]:
        assert any(entry.startswith(root) for entry in packages)


def test_paddle_lock_matches_selected_cpu_or_cuda_wheel(tmp_path: Path) -> None:
    for driver, acceleration in [
        (None, "cpu"),
        ("535.104.05", "nvidia-cu118"),
        ("560.94", "nvidia-cu126"),
        ("570.00", "nvidia-cu129"),
    ]:
        profile = HardwareProfile("linux", "x86_64", "NVIDIA GPU", driver, 8192, 16384)
        plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))
        for name in ("paddleocr", "paddleocr-vl"):
            backend = plan.backend(name)
            argv = _lock_step(backend).argv
            assert "--index-strategy" in argv
            assert argv[argv.index("--index-strategy") + 1] == "unsafe-first-match"
            lock = _lock_path(backend)
            assert acceleration in lock.name
            assert lock.is_file()
            expected = "paddlepaddle-gpu==" if acceleration != "cpu" else "paddlepaddle=="
            assert any(entry.startswith(expected) for entry in _locked_requirements(lock))


def test_frozen_backend_packages_and_smoke_checks_all_runtime_locks() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "scribeforge_backend.spec").read_text(encoding="utf-8")
    smoke = (root / ".github" / "workflows" / "windows-desktop.yml").read_text(encoding="utf-8")

    assert 'SOURCE / "scribeforge" / "runtime" / "locks"' in spec
    assert '"worker-src/scribeforge/runtime/locks"' in spec
    assert "runtime-locks" in smoke
    assert "py312-universal.txt" in smoke


def test_frozen_backend_collects_and_checks_opencc_conversion_data() -> None:
    root = Path(__file__).resolve().parents[1]
    spec = (root / "packaging" / "scribeforge_backend.spec").read_text(encoding="utf-8")
    smoke = (root / ".github" / "workflows" / "windows-desktop.yml").read_text(encoding="utf-8")

    assert 'collect_all("opencc")' in spec
    assert "opencc_datas" in spec
    assert "opencc_binaries" in spec
    assert "opencc_hidden_imports" in spec
    assert "opencc/clib/share/opencc" in smoke
    assert "t2s.json" in smoke
    assert "TSCharacters.ocd2" in smoke
