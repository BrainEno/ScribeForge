from __future__ import annotations

from scribeforge.runtime.models import RuntimeLayout


def build_runtime_environment(layout: RuntimeLayout) -> dict[str, str]:
    cache = layout.cache_dir
    models = layout.models_dir
    return {
        "UV_CACHE_DIR": str(cache / "uv"),
        "UV_PYTHON_INSTALL_DIR": str(layout.tools_dir / "python"),
        "HF_HOME": str(cache / "huggingface"),
        "MODELSCOPE_CACHE": str(cache / "modelscope"),
        "PADDLE_HOME": str(cache / "paddle"),
        "PADDLE_PDX_CACHE_HOME": str(models / "paddleocr"),
        "MINERU_HOME": str(models / "mineru"),
    }
