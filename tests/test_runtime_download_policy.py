from pathlib import Path

from scribeforge.runtime.models import (
    DEFAULT_HEALTH_CHECK_TIMEOUT_SECONDS,
    DEFAULT_INSTALL_STEP_TIMEOUT_SECONDS,
    DEFAULT_MODEL_STEP_TIMEOUT_SECONDS,
    HardwareProfile,
    RuntimeLayout,
)
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


def test_direct_runtime_versions_and_mineru_model_source_are_fixed(tmp_path: Path) -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))

    mineru = plan.backend("mineru")
    mineru_install = next(step for step in mineru.steps if step.name == "install")
    assert "mineru==4.1.7" in mineru_install.argv
    assert all("mineru>=4.0,<5" not in step.argv for step in mineru.steps)
    download = mineru.model_steps[0].argv
    assert download[download.index("--source") + 1] == "huggingface"
    assert download[download.index("--small-backend") + 1] == "onnx"
    assert download[download.index("--vlm-engine") + 1] == "llama-cpp"

    paddle = plan.backend("paddleocr")
    paddle_install = next(step for step in paddle.steps if step.name == "install")
    assert "paddleocr==3.3.0" in paddle_install.argv
    assert "paddlex==3.3.0" in paddle_install.argv
    assert "surya-ocr==0.14.0" in plan.backend("surya").steps[-1].argv


def test_install_model_and_health_steps_have_distinct_overall_deadlines(
    tmp_path: Path,
) -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))

    for backend in plan.backends:
        assert all(
            step.timeout_seconds == DEFAULT_INSTALL_STEP_TIMEOUT_SECONDS
            for step in backend.steps
        )
        assert all(
            step.timeout_seconds == DEFAULT_MODEL_STEP_TIMEOUT_SECONDS
            for step in backend.model_steps
        )
        assert all(
            step.timeout_seconds == DEFAULT_HEALTH_CHECK_TIMEOUT_SECONDS
            for step in backend.health_checks
        )
