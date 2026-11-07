from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.runtime.manager import RuntimeInstallError, RuntimeManager
from scribeforge.runtime.models import InstallStep, RuntimeBackendPlan, RuntimePlan


class EnvironmentExecutor:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.fail_on = fail_on
        self.calls: list[tuple[str, ...]] = []
        self.final_markers_during_health: list[str] = []

    def run(
        self,
        argv: tuple[str, ...],
        env: dict[str, str] | None = None,
        *,
        timeout_seconds: float,
    ) -> None:
        self.calls.append(argv)
        if argv[0] == "venv":
            environment = Path(argv[-1])
            environment.mkdir(parents=True)
            (environment / "marker.txt").write_text("new-v2", encoding="utf-8")
            return
        if argv[0] == "health":
            final = Path(argv[-1]).parent / "paddleocr"
            marker = final / "marker.txt"
            self.final_markers_during_health.append(marker.read_text(encoding="utf-8"))
        if argv[0] == self.fail_on:
            raise OSError(f"simulated {argv[0]} failure")


def _plan(root: Path, *, fail_on: str | None = None) -> tuple[RuntimePlan, EnvironmentExecutor]:
    final = root / "envs" / "paddleocr"
    backend = RuntimeBackendPlan(
        name="paddleocr",
        environment_dir=final,
        acceleration="cpu",
        steps=(
            InstallStep("environment", ("venv", str(final))),
            InstallStep("install", ("install", str(final / "bin" / "python"))),
        ),
        health_checks=(
            InstallStep("verify", ("health", str(final))),
            InstallStep("python", ("python", str(final / "bin" / "python"))),
        ),
        transactional_environment=True,
    )
    return RuntimePlan(root, (backend,)), EnvironmentExecutor(fail_on=fail_on)


def _old_environment(root: Path) -> Path:
    final = root / "envs" / "paddleocr"
    final.mkdir(parents=True)
    (final / "marker.txt").write_text("old-v1", encoding="utf-8")
    return final


def _staging_directories(root: Path) -> list[Path]:
    return list((root / "envs").glob(".paddleocr.staging-*"))


@pytest.mark.parametrize("fail_on", ["install", "python"])
def test_failed_install_or_health_preserves_old_environment(
    tmp_path: Path, fail_on: str
) -> None:
    final = _old_environment(tmp_path)
    plan, executor = _plan(tmp_path, fail_on=fail_on)
    manager = RuntimeManager(tmp_path, executor)

    with pytest.raises(RuntimeInstallError):
        manager.repair(plan, "paddleocr")

    assert (final / "marker.txt").read_text(encoding="utf-8") == "old-v1"
    assert _staging_directories(tmp_path) == []
    assert all(str(final) not in call[-1] for call in executor.calls if call[0] == "venv")


def test_success_publishes_only_after_health_and_uses_staging_executables(
    tmp_path: Path,
) -> None:
    final = _old_environment(tmp_path)
    plan, executor = _plan(tmp_path)

    RuntimeManager(tmp_path, executor).repair(plan, "paddleocr")

    assert executor.final_markers_during_health == ["old-v1"]
    assert (final / "marker.txt").read_text(encoding="utf-8") == "new-v2"
    assert _staging_directories(tmp_path) == []
    assert not list((tmp_path / "envs").glob(".paddleocr.previous-*"))
    for call in executor.calls:
        if call[0] in {"install", "python"}:
            assert ".paddleocr.staging-" in call[-1]


def test_publish_failure_restores_old_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = _old_environment(tmp_path)
    plan, executor = _plan(tmp_path)
    original_replace = Path.replace

    def fail_publish(source: Path, target: Path) -> Path:
        if ".paddleocr.staging-" in source.name and target == final:
            raise OSError("simulated publish failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_publish)
    events = []

    with pytest.raises(RuntimeInstallError, match="paddleocr:publish"):
        RuntimeManager(tmp_path, executor, on_event=events.append).repair(
            plan, "paddleocr"
        )

    assert any(event.step == "publish" and event.state == "failed" for event in events)
    assert (final / "marker.txt").read_text(encoding="utf-8") == "old-v1"
    assert _staging_directories(tmp_path) == []
