from pathlib import Path

from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan


def test_windows_nvidia_driver_uses_cu126_paddle_runtime(tmp_path: Path) -> None:
    profile = HardwareProfile(
        os_name="windows",
        architecture="x86_64",
        gpu_name="NVIDIA GeForce RTX 5080",
        nvidia_driver="560.94",
        vram_mb=16384,
        ram_mb=20480,
    )

    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))

    paddle = plan.backend("paddleocr")
    assert paddle.acceleration == "nvidia-cu126"
    assert any("paddlepaddle-gpu==3.2.0" in argument for step in paddle.steps for argument in step.argv)
    assert any("stable/cu126" in argument for step in paddle.steps for argument in step.argv)


def test_machine_without_nvidia_uses_cpu_paddle_runtime(tmp_path: Path) -> None:
    profile = HardwareProfile(
        os_name="windows",
        architecture="x86_64",
        gpu_name=None,
        nvidia_driver=None,
        vram_mb=None,
        ram_mb=16384,
    )

    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))

    paddle = plan.backend("paddleocr")
    assert paddle.acceleration == "cpu"
    assert any("paddlepaddle==3.2.0" in argument for step in paddle.steps for argument in step.argv)
    assert all("paddlepaddle-gpu" not in argument for step in paddle.steps for argument in step.argv)


def test_mineru_and_paddle_use_separate_private_environments(tmp_path: Path) -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    layout = RuntimeLayout(tmp_path)

    plan = build_runtime_plan(profile, layout)

    assert plan.backend("mineru").environment_dir == layout.environment_dir("mineru")
    assert plan.backend("paddleocr").environment_dir == layout.environment_dir("paddleocr")
    assert plan.backend("mineru").environment_dir != plan.backend("paddleocr").environment_dir


def test_old_nvidia_driver_falls_back_to_cpu_instead_of_guessing_gpu_wheel(tmp_path: Path) -> None:
    profile = HardwareProfile(
        os_name="windows",
        architecture="x86_64",
        gpu_name="NVIDIA GPU",
        nvidia_driver="440.00",
        vram_mb=8192,
        ram_mb=16384,
    )

    plan = build_runtime_plan(profile, RuntimeLayout(tmp_path))

    assert plan.backend("paddleocr").acceleration == "cpu"
