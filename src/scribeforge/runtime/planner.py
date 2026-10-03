from __future__ import annotations

from scribeforge.runtime.environment import build_runtime_environment
from scribeforge.runtime.models import (
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


def _paddle_acceleration(profile: HardwareProfile) -> tuple[str, str, str]:
    if profile.gpu_name and profile.nvidia_driver:
        if _driver_at_least(profile.nvidia_driver, (550, 54, 14)):
            return (
                "nvidia-cu126",
                "paddlepaddle-gpu==3.2.0",
                "https://www.paddlepaddle.org.cn/packages/stable/cu126/",
            )
        minimum = (452, 39) if profile.os_name == "windows" else (450, 80, 2)
        if _driver_at_least(profile.nvidia_driver, minimum):
            return (
                "nvidia-cu118",
                "paddlepaddle-gpu==3.2.0",
                "https://www.paddlepaddle.org.cn/packages/stable/cu118/",
            )
    return (
        "cpu",
        "paddlepaddle==3.2.0",
        "https://www.paddlepaddle.org.cn/packages/stable/cpu/",
    )


def _venv_steps(
    backend: str, layout: RuntimeLayout, uv_executable: str
) -> tuple[InstallStep, InstallStep]:
    environment = layout.environment_dir(backend)
    return (
        InstallStep("python", (uv_executable, "python", "install", "3.12")),
        InstallStep(
            "environment",
            (uv_executable, "venv", "--python", "3.12", str(environment)),
        ),
    )


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
            InstallStep(
                "install",
                (
                    uv_executable,
                    "pip",
                    "install",
                    "--python",
                    str(mineru_python),
                    "-U",
                    "mineru>=4.0,<5",
                ),
            ),
        ),
        model_steps=(
            InstallStep(
                "standard-models",
                (str(mineru_cli), "models", "download", "--tier", "standard"),
            ),
        ),
        health_checks=(
            InstallStep(
                "model-integrity",
                (str(mineru_cli), "models", "verify", "--tier", "standard"),
            ),
            InstallStep("runtime-version", (str(mineru_main), "version", "--json")),
        ),
        environment=runtime_environment,
    )

    acceleration, paddle_package, paddle_index = _paddle_acceleration(profile)
    paddle_python = layout.python_executable("paddleocr", profile.os_name)
    paddle_prefix = _venv_steps("paddleocr", layout, uv_executable)
    paddle_prefetch = (
        "from paddleocr import PaddleOCR; "
        "PaddleOCR(ocr_version='PP-OCRv6', use_doc_orientation_classify=False, "
        "use_doc_unwarping=False, use_textline_orientation=False)"
    )
    paddle_health = (
        "import paddle, paddleocr; "
        "from paddleocr import PaddleOCR; "
        "PaddleOCR(ocr_version='PP-OCRv6', use_doc_orientation_classify=False, "
        "use_doc_unwarping=False, use_textline_orientation=False); "
        "print(paddle.__version__)"
    )
    paddle = RuntimeBackendPlan(
        name="paddleocr",
        environment_dir=layout.environment_dir("paddleocr"),
        acceleration=acceleration,
        steps=paddle_prefix
        + (
            InstallStep(
                "engine",
                (
                    uv_executable,
                    "pip",
                    "install",
                    "--python",
                    str(paddle_python),
                    "-U",
                    paddle_package,
                    "-i",
                    paddle_index,
                ),
            ),
            InstallStep(
                "install",
                (
                    uv_executable,
                    "pip",
                    "install",
                    "--python",
                    str(paddle_python),
                    "-U",
                    "paddleocr>=3,<4",
                ),
            ),
        ),
        model_steps=(InstallStep("pp-ocrv6-models", (str(paddle_python), "-c", paddle_prefetch)),),
        health_checks=(InstallStep("runtime-and-models", (str(paddle_python), "-c", paddle_health)),),
        environment=runtime_environment,
    )

    return RuntimePlan(root=layout.root, backends=(mineru, paddle))
