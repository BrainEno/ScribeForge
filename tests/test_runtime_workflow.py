from pathlib import Path

import pytest

from scribeforge.runtime.manager import RuntimeEvent, RuntimeInstallError, RuntimeStatus
from scribeforge.runtime.models import HardwareProfile
from scribeforge.runtime.tools import RuntimeToolMissing
from scribeforge.runtime.workflow import RuntimeSetupWorkflow


def _profile() -> HardwareProfile:
    return HardwareProfile("windows", "AMD64", None, None, None, 16384)


def _missing_uv(*_args: object) -> str:
    raise RuntimeToolMissing("missing uv")


def test_inspect_reports_backend_state_and_missing_bootstrap(tmp_path: Path) -> None:
    workflow = RuntimeSetupWorkflow(
        tmp_path,
        hardware_probe=_profile,
        uv_resolver=_missing_uv,
    )

    overview = workflow.inspect()

    assert overview.hardware == _profile()
    assert overview.uv_available is False
    assert overview.mineru == RuntimeStatus("missing")
    assert overview.paddleocr == RuntimeStatus("missing")


def test_install_continues_with_paddle_when_mineru_fails(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    events: list[RuntimeEvent] = []

    class RecordingExecutor:
        def run(self, argv: tuple[str, ...], env: dict[str, str] | None = None) -> None:
            calls.append(argv)
            if "envs/mineru" in " ".join(argv).replace("\\", "/"):
                raise RuntimeError("synthetic MinerU failure")

    layout_root = tmp_path / "runtime"

    workflow = RuntimeSetupWorkflow(
        layout_root,
        hardware_probe=_profile,
        uv_ensurer=lambda tools, _os, _arch: str(tools / "uv.exe"),
        uv_resolver=_missing_uv,
        executor=RecordingExecutor(),
    )

    with pytest.raises(RuntimeInstallError, match="mineru:environment"):
        workflow.install(on_event=events.append)

    overview = workflow.inspect()
    assert overview.mineru.state == "failed"
    assert overview.mineru.failed_step == "environment"
    assert overview.paddleocr.state == "ready"
    assert any(event.backend == "mineru" and event.state == "failed" for event in events)
    assert any(event.backend == "paddleocr" and event.state == "ready" for event in events)
    assert any("envs/paddleocr" in " ".join(argv).replace("\\", "/") for argv in calls)
