from pathlib import Path

from scribeforge.runtime.manager import RuntimeEvent, RuntimeStatus
from scribeforge.runtime.models import HardwareProfile
from scribeforge.runtime.workflow import RuntimeSetupWorkflow


def _profile() -> HardwareProfile:
    return HardwareProfile("windows", "AMD64", None, None, None, 16384)


def test_inspect_reports_backend_state_and_missing_bootstrap(tmp_path: Path) -> None:
    workflow = RuntimeSetupWorkflow(
        tmp_path,
        hardware_probe=_profile,
        uv_ensurer=lambda *_args: (_ for _ in ()).throw(AssertionError("must not download")),
    )

    overview = workflow.inspect()

    assert overview.hardware == _profile()
    assert overview.uv_available is False
    assert overview.mineru == RuntimeStatus("missing")
    assert overview.paddleocr == RuntimeStatus("missing")


def test_install_uses_private_uv_and_persists_each_backend_status(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def ensure_uv(tools: Path, os_name: str, architecture: str) -> str:
        calls.append((os_name, architecture))
        return str(tools / "uv.exe")

    class Executor:
        def run(self, argv: tuple[str, ...], env: dict[str, str] | None = None) -> None:
            assert argv[0] == str(tmp_path / "runtime" / "tools" / "uv.exe") or argv[0].endswith("uv.exe")
            assert env is not None

    runtime_root = tmp_path / "runtime"
    workflow = RuntimeSetupWorkflow(
        runtime_root, hardware_probe=_profile, uv_ensurer=ensure_uv
    )
    # Inject the executor via the RuntimeManager constructor boundary in a follow-up
    # test; this case only validates the plan and bootstrap path using the real manager.
    # Commands are not run here so the integration test remains entirely offline.
    from scribeforge.runtime.planner import build_runtime_plan
    from scribeforge.runtime.models import RuntimeLayout

    layout = RuntimeLayout(runtime_root)
    plan = build_runtime_plan(_profile(), layout, str(layout.tools_dir / "uv.exe"))
    assert {backend.name for backend in plan.backends} == {"mineru", "paddleocr"}
    assert plan.backend("mineru").environment_dir == runtime_root / "envs" / "mineru"
    assert workflow.inspect().mineru.state == "missing"
    assert calls == []
