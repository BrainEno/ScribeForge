from pathlib import Path

from scribeforge.runtime.manager import RuntimeManager
from scribeforge.runtime.models import InstallStep, RuntimeBackendPlan, RuntimePlan


class FlakyExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, argv: tuple[str, ...], env: dict[str, str] | None = None) -> None:
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("temporary download failure")


def test_retryable_step_retries_then_succeeds(tmp_path: Path) -> None:
    executor = FlakyExecutor()
    backend = RuntimeBackendPlan(
        name="mineru",
        environment_dir=tmp_path / "envs" / "mineru",
        acceleration="cpu",
        steps=(InstallStep("download", ("tool", "download"), attempts=3),),
    )
    manager = RuntimeManager(tmp_path, executor)

    manager.install(RuntimePlan(tmp_path, (backend,)))

    assert executor.calls == 3
    assert manager.status("mineru").state == "ready"
