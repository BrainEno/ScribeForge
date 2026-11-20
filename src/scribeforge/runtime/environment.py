from __future__ import annotations

from pathlib import Path

from scribeforge.runtime.location import external_model_location
from scribeforge.runtime.models import RuntimeLayout


def paddle_model_root(layout: RuntimeLayout) -> Path:
    return (external_model_location() or layout.models_dir) / "paddleocr" / "verified"


def build_runtime_environment(layout: RuntimeLayout) -> dict[str, str]:
    cache = layout.cache_dir
    models = external_model_location() or layout.models_dir
    return {
        "UV_CACHE_DIR": str(cache / "uv"),
        "UV_PYTHON_INSTALL_DIR": str(layout.tools_dir / "python"),
        "HF_HOME": str(cache / "huggingface"),
        "MODELSCOPE_CACHE": str(cache / "modelscope"),
        "PADDLE_HOME": str(cache / "paddle"),
        "PADDLE_PDX_CACHE_HOME": str(layout.models_dir / "paddleocr"),
        "SCRIBEFORGE_PADDLE_MODELS_ROOT": str(paddle_model_root(layout)),
        "MINERU_HOME": str(models / "mineru"),
        "MINERU_MODEL_SOURCE": "local",
        "MINERU_MODEL_SMALL_BACKEND": "onnx",
        "MINERU_MODEL_VLM_ENGINE": "llama-cpp",
    }
