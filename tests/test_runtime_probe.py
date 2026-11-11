import subprocess
from types import SimpleNamespace

import pytest

from scribeforge.runtime import probe
from scribeforge.runtime.probe import parse_nvidia_smi


def test_parse_nvidia_smi_extracts_first_gpu() -> None:
    profile = parse_nvidia_smi("NVIDIA GeForce RTX 5080, 560.94, 16384\n")

    assert profile == ("NVIDIA GeForce RTX 5080", "560.94", 16384)


def test_parse_nvidia_smi_returns_none_for_empty_or_malformed_output() -> None:
    assert parse_nvidia_smi("") is None
    assert parse_nvidia_smi("only,two") is None
    assert parse_nvidia_smi("GPU, 560.94, unknown") is None


def test_nvidia_profile_runs_query_and_parses_result(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout="NVIDIA GeForce RTX 5080, 560.94, 16384\n")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe._nvidia_profile(["nvidia-smi"]) == (
        "NVIDIA GeForce RTX 5080",
        "560.94",
        16384,
    )


def test_nvidia_profile_returns_none_when_command_is_unavailable(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe._nvidia_profile(["nvidia-smi"]) is None


def test_probe_hardware_combines_platform_and_gpu_data(monkeypatch) -> None:
    monkeypatch.setattr(probe.platform, "system", lambda: "Windows")
    monkeypatch.setattr(probe.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(
        probe,
        "_nvidia_profile",
        lambda *, timeout_seconds: ("NVIDIA GeForce RTX 5080", "560.94", 16384),
    )
    monkeypatch.setattr(probe, "_ram_mb", lambda *, timeout_seconds: 20480)

    profile = probe.probe_hardware()

    assert profile.os_name == "windows"
    assert profile.architecture == "amd64"
    assert profile.gpu_name == "NVIDIA GeForce RTX 5080"
    assert profile.nvidia_driver == "560.94"
    assert profile.vram_mb == 16384
    assert profile.ram_mb == 20480


def test_probe_hardware_handles_machine_without_nvidia(monkeypatch) -> None:
    monkeypatch.setattr(probe.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(probe.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(probe, "_nvidia_profile", lambda *, timeout_seconds: None)
    monkeypatch.setattr(probe, "_ram_mb", lambda *, timeout_seconds: 32768)

    profile = probe.probe_hardware()

    assert profile.os_name == "macos"
    assert profile.gpu_name is None
    assert profile.nvidia_driver is None
    assert profile.vram_mb is None


def test_probe_hardware_forwards_configurable_deadlines(monkeypatch) -> None:
    calls: list[tuple[str, float]] = []
    monkeypatch.setattr(probe.platform, "system", lambda: "Linux")
    monkeypatch.setattr(probe.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        probe,
        "_nvidia_profile",
        lambda *, timeout_seconds: calls.append(("gpu", timeout_seconds)),
    )
    monkeypatch.setattr(
        probe,
        "_ram_mb",
        lambda *, timeout_seconds: calls.append(("ram", timeout_seconds)) or 8192,
    )

    profile = probe.probe_hardware(timeout_seconds=2.5)

    assert calls == [("gpu", 2.5), ("ram", 2.5)]
    assert profile.ram_mb == 8192


def test_nvidia_profile_returns_none_on_nonzero_exit(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["nvidia-smi"])

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe._nvidia_profile(["nvidia-smi"]) is None


def test_nvidia_profile_uses_the_injected_command(monkeypatch) -> None:
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return SimpleNamespace(stdout="GPU, 1.0, 1024\n")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe._nvidia_profile(["custom-smi", "--flag"]) == ("GPU", "1.0", 1024)
    assert seen == [["custom-smi", "--flag"]]


def _force_posix(monkeypatch, sysconf) -> None:
    monkeypatch.setattr(probe.os, "name", "posix")
    monkeypatch.setattr(probe.os, "sysconf", sysconf, raising=False)


def test_ram_mb_posix_sysconf_failure_returns_none(monkeypatch) -> None:
    def broken_sysconf(name: str) -> int:
        raise OSError("no sysconf")

    _force_posix(monkeypatch, broken_sysconf)

    assert probe._ram_mb() is None


def test_ram_mb_posix_sysconf_value_error_returns_none(monkeypatch) -> None:
    def broken_sysconf(name: str) -> int:
        raise ValueError("bad value")

    _force_posix(monkeypatch, broken_sysconf)

    assert probe._ram_mb() is None


def test_ram_mb_windows_powershell_success(monkeypatch) -> None:
    monkeypatch.setattr(probe.os, "name", "nt")

    def fake_run(argv, **kwargs):
        assert argv[0] == "powershell"
        return SimpleNamespace(stdout="8192\n")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe._ram_mb() == 8192


@pytest.mark.parametrize(
    "failure",
    [
        OSError("no powershell"),
        subprocess.TimeoutExpired(cmd=["powershell"], timeout=10),
    ],
    ids=["oserror", "timeout"],
)
def test_ram_mb_windows_failure_returns_none(monkeypatch, failure: Exception) -> None:
    monkeypatch.setattr(probe.os, "name", "nt")

    def fake_run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(probe.subprocess, "run", fake_run)

    assert probe._ram_mb() is None


def test_ram_mb_unknown_os_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(probe.os, "name", "plan9")
    assert probe._ram_mb() is None


def test_ram_mb_posix_computes_memory_from_sysconf(monkeypatch) -> None:
    def fake_sysconf(name: str) -> int:
        return {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 2_000_000}[name]

    _force_posix(monkeypatch, fake_sysconf)

    assert probe._ram_mb() == 4096 * 2_000_000 // (1024 * 1024)


def test_probe_hardware_rejects_nonpositive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        probe.probe_hardware(timeout_seconds=0)
