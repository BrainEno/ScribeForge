from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from scribeforge.adapters.runtime_workers import worker_source_root
from scribeforge.runtime.environment import build_runtime_environment, paddle_model_root
from scribeforge.runtime.models import (
    DEFAULT_HEALTH_CHECK_TIMEOUT_SECONDS,
    DEFAULT_MODEL_STEP_TIMEOUT_SECONDS,
    HardwareProfile,
    InstallStep,
    RuntimeBackendPlan,
    RuntimeLayout,
    RuntimePlan,
)


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in value.split("."):
        digits = "".join(character for character in piece if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _driver_at_least(driver: str | None, minimum: tuple[int, ...]) -> bool:
    if driver is None:
        return False
    actual = _version_tuple(driver)
    padded_length = max(len(actual), len(minimum))
    actual += (0,) * (padded_length - len(actual))
    expected = minimum + (0,) * (padded_length - len(minimum))
    return actual >= expected


def _paddle_acceleration(profile: HardwareProfile) -> tuple[str, str]:
    """Pick the PaddlePaddle wheel that can actually run on this machine.

    The CUDA 12.6 build aborts with ``Unsupported GPU architecture`` on Blackwell
    parts, so drivers from the 570 series onward get the CUDA 12.9 build.
    """

    if profile.gpu_name and profile.nvidia_driver:
        if _driver_at_least(profile.nvidia_driver, (570, 0)):
            return (
                "nvidia-cu129",
                "https://www.paddlepaddle.org.cn/packages/stable/cu129/",
            )
        if _driver_at_least(profile.nvidia_driver, (550, 54, 14)):
            return (
                "nvidia-cu126",
                "https://www.paddlepaddle.org.cn/packages/stable/cu126/",
            )
        minimum = (452, 39) if profile.os_name == "windows" else (450, 80, 2)
        if _driver_at_least(profile.nvidia_driver, minimum):
            return (
                "nvidia-cu118",
                "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
            )
    return (
        "cpu",
        "https://www.paddlepaddle.org.cn/packages/stable/cpu/",
    )


def _venv_steps(
    backend: str, layout: RuntimeLayout, uv_executable: str
) -> tuple[InstallStep, InstallStep]:
    environment = layout.environment_dir(backend)
    return (
        InstallStep("python", (uv_executable, "python", "install", "3.12"), attempts=3),
        InstallStep(
            "environment",
            (
                uv_executable,
                "venv",
                "--relocatable",
                "--python",
                "3.12",
                str(environment),
            ),
        ),
    )


def _locked_install_step(
    uv_executable: str,
    python: Path,
    lock_name: str,
    *,
    index: str | None = None,
) -> InstallStep:
    lock = worker_source_root() / "scribeforge" / "runtime" / "locks" / lock_name
    argv: tuple[str, ...] = (
        uv_executable,
        "pip",
        "install",
        "--require-hashes",
        "--no-deps",
        "--no-config",
        "--default-index",
        "https://pypi.org/simple",
        "--python",
        str(python),
        "-r",
        str(lock),
    )
    if index is not None:
        argv += (
            "--extra-index-url",
            index,
            "--index-strategy",
            "unsafe-first-match",
        )
    return InstallStep("install", argv, attempts=3)


def _environment_items(layout: RuntimeLayout) -> tuple[tuple[str, str], ...]:
    return tuple(build_runtime_environment(layout).items())


def build_runtime_plan(
    profile: HardwareProfile,
    layout: RuntimeLayout,
    uv_executable: str = "uv",
) -> RuntimePlan:
    runtime_environment = _environment_items(layout)

    mineru_python = layout.python_executable("mineru", profile.os_name)
    mineru_cli = layout.executable("mineru", "mineru-kit", profile.os_name)
    mineru_main = layout.executable("mineru", "mineru", profile.os_name)
    mineru_prefix = _venv_steps("mineru", layout, uv_executable)
    mineru = RuntimeBackendPlan(
        name="mineru",
        environment_dir=layout.environment_dir("mineru"),
        acceleration="compatible-local",
        steps=mineru_prefix
        + (
            _locked_install_step(
                uv_executable,
                mineru_python,
                "mineru-py312-universal.txt",
            ),
        ),
        model_steps=(
            InstallStep(
                "standard-models",
                (
                    str(mineru_cli),
                    "models",
                    "download",
                    "--tier",
                    "standard",
                    "--source",
                    "huggingface",
                    "--small-backend",
                    "onnx",
                    "--vlm-engine",
                    "llama-cpp",
                ),
                attempts=3,
            ),
        ),
        health_checks=(
            InstallStep(
                "model-integrity",
                (
                    str(mineru_cli),
                    "models",
                    "verify",
                    "--tier",
                    "standard",
                    "--small-backend",
                    "onnx",
                    "--vlm-engine",
                    "llama-cpp",
                ),
            ),
            InstallStep("runtime-version", (str(mineru_main), "version", "--json")),
        ),
        environment=runtime_environment,
    )

    acceleration, paddle_index = _paddle_acceleration(profile)
    paddle_python = layout.python_executable("paddleocr", profile.os_name)
    paddle_prefix = _venv_steps("paddleocr", layout, uv_executable)
    paddle_models = paddle_model_root(layout)
    paddle_model_setup = (
        worker_source_root() / "scribeforge" / "workers" / "paddle_model_setup.py"
    )
    paddle_health = (
        "import paddle, paddleocr; "
        "from paddleocr import PaddleOCR; "
        "PaddleOCR(ocr_version='PP-OCRv4', return_word_box=True, "
        "text_detection_model_name='PP-OCRv4_mobile_det', "
        f"text_detection_model_dir={str(paddle_models / 'PP-OCRv4_mobile_det')!r}, "
        "text_recognition_model_name='PP-OCRv4_mobile_rec', "
        f"text_recognition_model_dir={str(paddle_models / 'PP-OCRv4_mobile_rec')!r}, "
        "use_doc_orientation_classify=False, use_doc_unwarping=False, "
        "use_textline_orientation=False); "
        "print(paddle.__version__)"
    )
    paddle = RuntimeBackendPlan(
        name="paddleocr",
        environment_dir=layout.environment_dir("paddleocr"),
        acceleration=acceleration,
        steps=paddle_prefix
        + (
            _locked_install_step(
                uv_executable,
                paddle_python,
                f"paddleocr-{acceleration}-py312-universal.txt",
                index=paddle_index,
            ),
        ),
        model_steps=(
            InstallStep(
                "pp-ocrv4-word-box-models",
                (
                    str(paddle_python),
                    str(paddle_model_setup),
                    "--models-dir",
                    str(paddle_models),
                ),
                attempts=3,
            ),
        ),
        health_checks=(
            InstallStep("runtime-and-models", (str(paddle_python), "-c", paddle_health)),
        ),
        environment=runtime_environment,
    )

    # --- Optional backends (technology route R1 / R2) -----------------------
    # Neither is part of a default install: both download multi-gigabyte weights
    # that only specific work needs. ``scribeforge runtime repair --backend X``
    # installs one on demand.
    vl_python = layout.python_executable("paddleocr-vl", profile.os_name)
    vl_prefix = _venv_steps("paddleocr-vl", layout, uv_executable)
    vl_health = (
        "import paddle; "
        "from paddleocr import PaddleOCRVL; "
        "print(paddle.__version__)"
    )
    paddle_vl = RuntimeBackendPlan(
        name="paddleocr-vl",
        environment_dir=layout.environment_dir("paddleocr-vl"),
        acceleration=acceleration,
        steps=vl_prefix
        + (
            # PaddleOCR-VL ships inside PaddleOCR 3.x and uses the same full lock.
            _locked_install_step(
                uv_executable,
                vl_python,
                f"paddleocr-{acceleration}-py312-universal.txt",
                index=paddle_index,
            ),
        ),
        model_steps=(
            InstallStep(
                "vl-models",
                (
                    str(vl_python),
                    "-c",
                    "from paddleocr import PaddleOCRVL; PaddleOCRVL()",
                ),
                attempts=3,
            ),
        ),
        health_checks=(InstallStep("runtime-and-models", (str(vl_python), "-c", vl_health)),),
        environment=runtime_environment,
        optional=True,
    )

    surya_python = layout.python_executable("surya", profile.os_name)
    surya_prefix = _venv_steps("surya", layout, uv_executable)
    surya = RuntimeBackendPlan(
        name="surya",
        environment_dir=layout.environment_dir("surya"),
        acceleration="cpu-or-cuda",
        steps=surya_prefix
        + (
            _locked_install_step(
                uv_executable,
                surya_python,
                "surya-py312-universal.txt",
            ),
        ),
        model_steps=(
            InstallStep(
                "layout-models",
                (
                    str(surya_python),
                    "-c",
                    "from surya.layout import LayoutPredictor; LayoutPredictor()",
                ),
                attempts=3,
            ),
        ),
        health_checks=(
            InstallStep(
                "runtime-and-models",
                (
                    str(surya_python),
                    "-c",
                    (
                        "from importlib.metadata import version; "
                        "from surya.layout import LayoutPredictor; "
                        "print(version('surya-ocr'))"
                    ),
                ),
            ),
        ),
        environment=runtime_environment,
        optional=True,
    )

    def with_phase_deadlines(backend: RuntimeBackendPlan) -> RuntimeBackendPlan:
        return replace(
            backend,
            transactional_environment=True,
            model_steps=tuple(
                replace(step, timeout_seconds=DEFAULT_MODEL_STEP_TIMEOUT_SECONDS)
                for step in backend.model_steps
            ),
            health_checks=tuple(
                replace(step, timeout_seconds=DEFAULT_HEALTH_CHECK_TIMEOUT_SECONDS)
                for step in backend.health_checks
            ),
        )

    return RuntimePlan(
        root=layout.root,
        backends=tuple(
            with_phase_deadlines(backend)
            for backend in (mineru, paddle, paddle_vl, surya)
        ),
    )
