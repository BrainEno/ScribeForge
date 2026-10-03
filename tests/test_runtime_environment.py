from pathlib import Path

from scribeforge.runtime.environment import build_runtime_environment
from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan


def test_runtime_environment_routes_all_major_caches_under_private_root(tmp_path: Path) -> None:
    layout = RuntimeLayout(tmp_path)

    env = build_runtime_environment(layout)

    assert env["UV_CACHE_DIR"].startswith(str(tmp_path))
    assert env["UV_PYTHON_INSTALL_DIR"].startswith(str(tmp_path))
    assert env["HF_HOME"].startswith(str(tmp_path))
    assert env["MODELSCOPE_CACHE"].startswith(str(tmp_path))
    assert env["PADDLE_HOME"].startswith(str(tmp_path))
    assert env["PADDLE_PDX_CACHE_HOME"].startswith(str(tmp_path))
    assert env["MINERU_HOME"].startswith(str(tmp_path))


def test_planner_prefetches_and_verifies_mineru_standard_models(tmp_path: Path) -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))
    mineru = plan.backend("mineru")

    assert any(step.argv[-4:] == ("models", "download", "--tier", "standard") for step in mineru.model_steps)
    assert any(step.argv[-4:] == ("models", "verify", "--tier", "standard") for step in mineru.health_checks)
    assert dict(mineru.environment)["MINERU_HOME"].startswith(str(tmp_path))


def test_planner_prefetches_paddle_v6_into_private_cache(tmp_path: Path) -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))
    paddle = plan.backend("paddleocr")

    model_command = " ".join(paddle.model_steps[0].argv)
    assert "PaddleOCR" in model_command
    assert "PP-OCRv6" in model_command
    env = dict(paddle.environment)
    assert env["PADDLE_PDX_CACHE_HOME"].startswith(str(tmp_path))
    assert env["PADDLE_HOME"].startswith(str(tmp_path))
    assert paddle.health_checks
