from pathlib import Path

from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan


def test_model_downloads_have_retry_budget(tmp_path: Path) -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))

    assert plan.backend("mineru").model_steps[0].attempts == 3
    assert plan.backend("paddleocr").model_steps[0].attempts == 3


def test_health_checks_are_not_retried_as_downloads(tmp_path: Path) -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))

    assert all(step.attempts == 1 for step in plan.backend("mineru").health_checks)
    assert all(step.attempts == 1 for step in plan.backend("paddleocr").health_checks)
