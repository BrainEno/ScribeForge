from pathlib import Path, PureWindowsPath

from scribeforge.runtime.environment import build_runtime_environment
from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan


def test_paddle_setup_downloads_locked_models_and_health_uses_their_directories(
    tmp_path: Path,
) -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    layout = RuntimeLayout(tmp_path)

    paddle = build_runtime_plan(profile, layout).backend("paddleocr")
    download = paddle.model_steps[0].argv
    health = paddle.health_checks[0].argv[-1]

    assert download[1].endswith("paddle_model_setup.py")
    assert "--models-dir" in download
    models_root = Path(download[download.index("--models-dir") + 1])
    assert models_root == layout.models_dir / "paddleocr" / "verified"
    assert "text_detection_model_dir" in health
    assert "text_recognition_model_dir" in health
    # The health check is Python source: WindowsPath backslashes are repr-escaped.
    assert repr(str(models_root / "PP-OCRv4_mobile_det")) in health
    assert repr(str(models_root / "PP-OCRv4_mobile_rec")) in health


def test_health_command_repr_escapes_windows_model_paths() -> None:
    profile = HardwareProfile("windows", "x86_64", None, None, None, 16384)
    layout = RuntimeLayout(PureWindowsPath(r"C:\Users\Alice\ScribeForge\runtime"))  # type: ignore[arg-type]

    health = build_runtime_plan(profile, layout).backend("paddleocr").health_checks[0].argv[-1]
    models_root = layout.models_dir / "paddleocr" / "verified"

    assert repr(str(models_root / "PP-OCRv4_mobile_det")) in health
    assert repr(str(models_root / "PP-OCRv4_mobile_rec")) in health
    compile(health, "<paddle-health>", "exec")


def test_paddle_worker_receives_verified_model_root(tmp_path: Path) -> None:
    layout = RuntimeLayout(tmp_path)

    environment = build_runtime_environment(layout)

    assert environment["SCRIBEFORGE_PADDLE_MODELS_ROOT"] == str(
        layout.models_dir / "paddleocr" / "verified"
    )
