"""Tests for first-run disk budgeting (issue #214): sizes + preflight."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from scribeforge import cli as cli_module
from scribeforge.runtime import probe as runtime_probe
from scribeforge.runtime.sizes import (
    BACKEND_SIZE_ESTIMATES,
    HEADROOM_FACTOR,
    TOOLS_DOWNLOAD_BYTES,
    DiskUsage,
    estimate_backend,
    estimate_payload,
    format_bytes,
    preflight,
    required_bytes,
)


def test_all_planned_backends_have_estimates() -> None:
    for name in ("mineru", "paddleocr", "paddleocr-vl", "surya"):
        estimate = estimate_backend(name)
        assert estimate.name == name
        assert estimate.download_bytes > 0
        assert estimate.install_bytes > 0
        assert estimate.model_bytes > 0
        assert estimate.total_bytes == (
            estimate.download_bytes + estimate.install_bytes + estimate.model_bytes
        )
        assert estimate.note


def test_unknown_backend_falls_back_to_conservative_estimate() -> None:
    estimate = estimate_backend("some-future-backend")
    assert estimate.name == "some-future-backend"
    assert estimate.total_bytes > 0
    assert "保守" in estimate.note


def test_estimate_payload_is_json_serializable_and_keyed_by_backend() -> None:
    payload = estimate_payload(["mineru", "surya"])
    assert set(payload) == {"mineru", "surya"}
    json.dumps(payload)  # must not raise
    assert payload["mineru"]["total_bytes"] == BACKEND_SIZE_ESTIMATES["mineru"].total_bytes


def test_required_bytes_includes_tools_and_headroom() -> None:
    raw = (
        BACKEND_SIZE_ESTIMATES["mineru"].total_bytes
        + BACKEND_SIZE_ESTIMATES["paddleocr"].total_bytes
    )
    expected = int((raw + TOOLS_DOWNLOAD_BYTES) * HEADROOM_FACTOR)
    assert required_bytes(["mineru", "paddleocr"]) == expected
    # Empty selection needs no headroom on tools but must not crash.
    assert required_bytes([]) == 0


def _usage(free: int, total: int = 1_000_000_000_000) -> DiskUsage:
    return DiskUsage(total=total, used=total - free, free=free)


def test_preflight_sufficient_when_free_space_covers_requirement(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    required = required_bytes(["mineru", "paddleocr"])
    report = preflight(root, ["mineru", "paddleocr"], usage_probe=lambda p: _usage(required + 1))
    assert report["sufficient"] is True
    assert report["missing_bytes"] == 0
    assert report["required_bytes"] == required
    assert report["runtime_root"] == str(root)
    assert report["estimated"] is True
    assert "可以开始安装" in str(report["advice"])
    assert set(report["backends"]) == {"mineru", "paddleocr"}


def test_preflight_insufficient_reports_missing_and_actionable_advice(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    required = required_bytes(["mineru", "paddleocr"])
    free = required // 2
    report = preflight(root, ["mineru", "paddleocr"], usage_probe=lambda p: _usage(free))
    assert report["sufficient"] is False
    assert report["missing_bytes"] == required - free
    advice = str(report["advice"])
    assert "还差约" in advice
    assert "清理磁盘" in advice
    assert "--runtime-root" in advice


def test_preflight_uses_parent_when_runtime_root_missing(tmp_path: Path) -> None:
    root = tmp_path / "not-created-yet" / "runtime"
    probed: list[Path] = []

    def probe(path: Path) -> DiskUsage:
        probed.append(path)
        return _usage(10**12)

    report = preflight(root, ["mineru"], usage_probe=probe)
    # Neither the root nor its parent exists, so the probe walks up to the
    # nearest existing ancestor (tmp_path itself).
    assert probed == [tmp_path]
    assert report["path"] == str(tmp_path)
    assert report["runtime_root"] == str(root)
    assert report["sufficient"] is True


def test_preflight_result_is_json_serializable(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    report = preflight(
        root, ["mineru", "paddleocr", "paddleocr-vl", "surya"], usage_probe=lambda p: _usage(10**13)
    )
    json.dumps(report)
    assert report["backends"]["paddleocr-vl"]["total_bytes"] > 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0 B"),
        (512, "512 B"),
        (2048, "2.0 KB"),
        (5 * 1024**2, "5.0 MB"),
        (12 * 1024**3 + 512 * 1024**2, "12.5 GB"),
    ],
)
def test_format_bytes(value: int, expected: str) -> None:
    assert format_bytes(value) == expected


def test_runtime_event_carries_byte_counters() -> None:
    from scribeforge.runtime.manager import RuntimeEvent
    from scribeforge.runtime.sizes import BACKEND_SIZE_ESTIMATES

    event = RuntimeEvent("mineru", "models", "completed", None, 5, 5)
    assert event.downloaded_bytes == 0
    assert event.installed_bytes == 0
    full = RuntimeEvent(
        "mineru",
        "complete",
        "ready",
        None,
        5,
        5,
        BACKEND_SIZE_ESTIMATES["mineru"].download_bytes
        + BACKEND_SIZE_ESTIMATES["mineru"].model_bytes,
        BACKEND_SIZE_ESTIMATES["mineru"].install_bytes,
    )
    assert full.downloaded_bytes > full.installed_bytes


def test_setup_json_progress_and_retention(tmp_path: Path, capsys, monkeypatch) -> None:
    """Issue #214: setup --json streams byte counters and a retention policy."""
    monkeypatch.setattr(runtime_probe, "_nvidia_profile", lambda *, timeout_seconds: None)
    monkeypatch.setattr(runtime_probe, "_ram_mb", lambda *, timeout_seconds: None)
    from scribeforge.runtime.workflow import RuntimeSetupWorkflow

    monkeypatch.setattr(
        RuntimeSetupWorkflow,
        "install",
        lambda self, on_event=None: None,
    )
    monkeypatch.setattr(
        RuntimeSetupWorkflow,
        "inspect",
        lambda self: _FakeOverview(),
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    code = cli_module.main(["setup", "--runtime-root", str(runtime_root), "--json"])
    captured = capsys.readouterr()
    assert code == 0
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    result = lines[-1]
    assert result["type"] == "result"
    retention = result["retention"]
    assert "models" in retention["kept"]
    assert "staging" in " ".join(retention["cleaned"])
    assert "保留" in retention["policy"]


class _FakeStatus:
    state = "ready"
    failed_step = None


class _FakeOverview:
    hardware = runtime_probe.HardwareProfile(
        os_name="linux",
        architecture="x86_64",
        gpu_name=None,
        nvidia_driver=None,
        vram_mb=None,
        ram_mb=None,
    )
    uv_available = True
    mineru = _FakeStatus()
    paddleocr = _FakeStatus()
    optional_backends: ClassVar[dict[str, _FakeStatus]] = {
        "paddleocr-vl": _FakeStatus(),
        "surya": _FakeStatus(),
    }


class TestRuntimeCleanup:
    """Issue #214: runtime-clean frees disposable artifacts only."""

    def _make_runtime(self, tmp_path: Path) -> Path:
        from scribeforge.runtime.models import RuntimeLayout

        root = tmp_path / "runtime"
        layout = RuntimeLayout(root)
        staging = root / "envs" / ".mineru.staging-abc123"
        staging.mkdir(parents=True)
        (staging / "payload.bin").write_bytes(b"x" * 4096)
        previous = root / "envs" / ".paddleocr.previous-def456"
        previous.mkdir(parents=True)
        (previous / "old.bin").write_bytes(b"y" * 2048)
        layout.models_dir.mkdir(parents=True)
        (layout.models_dir / "model.bin").write_bytes(b"m" * 1024)
        layout.cache_dir.mkdir(parents=True)
        (layout.cache_dir / "c.bin").write_bytes(b"c" * 512)
        (root / "runtime-state.json").write_text("{}", encoding="utf-8")
        return root

    def test_clean_removes_staging_and_previous_but_keeps_models(self, tmp_path: Path) -> None:
        from scribeforge.runtime.cleanup import clean_runtime_artifacts

        root = self._make_runtime(tmp_path)
        report = clean_runtime_artifacts(root)
        assert report["freed_bytes"] == 4096 + 2048
        assert len(report["removed"]) == 2
        assert (root / "models" / "model.bin").exists()
        assert (root / "cache" / "c.bin").exists()
        assert (root / "runtime-state.json").exists()
        assert "models" in report["kept"]

    def test_clean_with_cache_and_models_flags(self, tmp_path: Path) -> None:
        from scribeforge.runtime.cleanup import clean_runtime_artifacts

        root = self._make_runtime(tmp_path)
        report = clean_runtime_artifacts(root, include_cache=True, include_models=True)
        assert report["freed_bytes"] == 4096 + 2048 + 512 + 1024
        assert not (root / "models").exists()
        assert not (root / "cache").exists()

    def test_clean_on_missing_root_is_a_no_op(self, tmp_path: Path) -> None:
        from scribeforge.runtime.cleanup import clean_runtime_artifacts

        report = clean_runtime_artifacts(tmp_path / "nope")
        assert report["freed_bytes"] == 0
        assert report["removed"] == []

    def test_cli_runtime_clean_json(self, tmp_path: Path, capsys) -> None:
        root = self._make_runtime(tmp_path)
        code = cli_module.main(["runtime-clean", "--runtime-root", str(root), "--json"])
        captured = capsys.readouterr()
        assert code == 0
        report = json.loads(captured.out)
        assert report["freed_bytes"] == 4096 + 2048
        assert report["runtime_root"] == str(root)
