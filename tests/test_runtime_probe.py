from types import SimpleNamespace

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
        lambda: ("NVIDIA GeForce RTX 5080", "560.94", 16384),
    )
    monkeypatch.setattr(probe, "_ram_mb", lambda: 20480)

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
    monkeypatch.setattr(probe, "_nvidia_profile", lambda: None)
    monkeypatch.setattr(probe, "_ram_mb", lambda: 32768)

    profile = probe.probe_hardware()

    assert profile.os_name == "macos"
    assert profile.gpu_name is None
    assert profile.nvidia_driver is None
    assert profile.vram_mb is None
