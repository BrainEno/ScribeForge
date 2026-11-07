from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from scribeforge.runtime.environment import paddle_model_root
from scribeforge.runtime.models import RuntimeLayout
from scribeforge.workers.paddle_model_setup import PADDLE_MODELS, verify_paddle_model


def record_evidence(
    evidence_dir: Path,
    runtime_root: Path,
    repository_root: Path,
    *,
    os_name: str = "linux",
) -> dict[str, Any]:
    """Independently hash real model files and record the managed distribution."""

    if os_name not in {"linux", "windows"}:
        raise ValueError(f"unsupported Paddle smoke platform: {os_name}")
    layout = RuntimeLayout(runtime_root.resolve())
    models_root = paddle_model_root(layout)
    models: list[dict[str, object]] = []
    for model in PADDLE_MODELS:
        model_directory = models_root / model.name
        verify_paddle_model(model, model_directory)
        files = []
        locked_files = (
            (model.weight_path, model.weight_sha256),
            *((locked.path, locked.sha256) for locked in model.metadata_files),
        )
        for relative, expected_sha256 in locked_files:
            path = model_directory / relative
            payload = path.read_bytes()
            actual_sha256 = hashlib.sha256(payload).hexdigest()
            if actual_sha256 != expected_sha256:
                raise RuntimeError(f"independent model digest mismatch: {path}")
            files.append(
                {
                    "path": relative,
                    "bytes": len(payload),
                    "sha256": actual_sha256,
                }
            )
        models.append(
            {
                "name": model.name,
                "revision": model.revision,
                "files": files,
            }
        )

    lock_path = repository_root / "src/scribeforge/runtime/locks/paddleocr-cpu-py312-universal.txt"
    managed_python = layout.python_executable("paddleocr", os_name)
    metadata_code = (
        "import json, sys; from importlib.metadata import version; "
        "print(json.dumps({'python': sys.version.split()[0], "
        "'paddlepaddle': version('paddlepaddle'), "
        "'paddleocr': version('paddleocr'), 'paddlex': version('paddlex')}))"
    )
    installed = json.loads(
        subprocess.run(
            [str(managed_python), "-c", metadata_code],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "scribeforge-real-paddle-runtime-evidence",
        "platform": os_name,
        "accuracy_evaluation": {
            "performed": False,
            "reason": "This smoke validates installation, exact model bytes, native word geometry and the worker protocol, not recognition accuracy.",
        },
        "installed_distributions": installed,
        "lock_file": {
            "path": lock_path.relative_to(repository_root).as_posix(),
            "sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        },
        "models": models,
        "synthetic_image_sha256": hashlib.sha256(
            (evidence_dir / "synthetic-paddle-smoke.png").read_bytes()
        ).hexdigest(),
    }
    (evidence_dir / "model-evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    state = layout.root / "runtime-state.json"
    if not state.is_file():
        raise RuntimeError(f"missing committed runtime state: {state}")
    shutil.copy2(state, evidence_dir / "runtime-state.json")
    print(f"recorded exact model and runtime evidence in {evidence_dir}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument("runtime_root", type=Path)
    parser.add_argument("repository_root", type=Path)
    parser.add_argument(
        "--os-name",
        choices=("linux", "windows"),
        default="windows" if os.name == "nt" else "linux",
    )
    args = parser.parse_args()
    record_evidence(
        args.evidence_dir,
        args.runtime_root,
        args.repository_root,
        os_name=args.os_name,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
