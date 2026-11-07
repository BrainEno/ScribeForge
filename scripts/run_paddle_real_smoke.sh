#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

: "${SCRIBEFORGE_RUNTIME_ROOT:?set SCRIBEFORGE_RUNTIME_ROOT to a new empty runtime directory}"
evidence_dir="${SCRIBEFORGE_PADDLE_SMOKE_EVIDENCE:-${TMPDIR:-/tmp}/scribeforge-paddle-smoke-evidence}"
mkdir -p "$evidence_dir"
evidence_dir=$(cd "$evidence_dir" && pwd)
export SCRIBEFORGE_PADDLE_SMOKE_EVIDENCE="$evidence_dir"

if [[ -e "$SCRIBEFORGE_RUNTIME_ROOT" && ! -d "$SCRIBEFORGE_RUNTIME_ROOT" ]]; then
    echo "runtime root exists and is not a directory: $SCRIBEFORGE_RUNTIME_ROOT" >&2
    exit 2
fi
if [[ -d "$SCRIBEFORGE_RUNTIME_ROOT" ]] && [[ -n $(find "$SCRIBEFORGE_RUNTIME_ROOT" -mindepth 1 -print -quit) ]]; then
    echo "refusing to modify a non-empty runtime root: $SCRIBEFORGE_RUNTIME_ROOT" >&2
    exit 2
fi
mkdir -p "$SCRIBEFORGE_RUNTIME_ROOT"

smoke_image="$evidence_dir/synthetic-paddle-smoke.png"
python - "$smoke_image" <<'PY'
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

output = Path(sys.argv[1])
image = Image.new("RGB", (1600, 500), "white")
draw = ImageDraw.Draw(image)
font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
font_large = ImageFont.truetype(font_path, 64)
font_small = ImageFont.truetype(font_path, 52)
draw.text((80, 90), "ScribeForge OCR 2026", fill="black", font=font_large)
draw.text((80, 260), "Native word boxes 0123456789", fill="black", font=font_small)
image.save(output)
print(f"synthetic smoke image: {output}")
PY

scribeforge setup --json --backend paddleocr \
    --runtime-root "$SCRIBEFORGE_RUNTIME_ROOT" \
    | tee "$evidence_dir/setup.jsonl"

python - "$evidence_dir" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from scribeforge.runtime.models import RuntimeLayout
from scribeforge.workers.paddle_model_setup import PADDLE_MODELS, verify_paddle_model

evidence_dir = Path(sys.argv[1])
layout = RuntimeLayout(Path(os.environ["SCRIBEFORGE_RUNTIME_ROOT"]).resolve())
models: list[dict[str, object]] = []
for model in PADDLE_MODELS:
    verify_paddle_model(model, layout.paddle_models_dir)
    files = []
    for relative, expected_sha256 in (
        (model.weight_filename, model.weight_sha256),
        ("config.json", model.config_sha256),
        ("inference.json", model.inference_json_sha256),
        ("inference.yml", model.inference_yml_sha256),
    ):
        path = layout.paddle_models_dir / model.name / relative
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

lock_path = Path.cwd() / "src/scribeforge/runtime/locks/paddleocr-cpu-py312-universal.txt"
managed_python = layout.python_executable("paddleocr", "linux")
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
report = {
    "schema_version": 1,
    "kind": "scribeforge-real-paddle-runtime-evidence",
    "accuracy_evaluation": {
        "performed": False,
        "reason": "This smoke validates installation, exact model bytes, native word geometry and the worker protocol, not recognition accuracy.",
    },
    "installed_distributions": installed,
    "lock_file": {
        "path": str(lock_path.relative_to(Path.cwd())),
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
PY

export SCRIBEFORGE_RUN_PADDLE_SMOKE=1
export SCRIBEFORGE_PADDLE_SMOKE_IMAGE="$smoke_image"
export SCRIBEFORGE_PADDLE_SMOKE_REPORT="$evidence_dir/worker-result.json"
python -m pytest -m gpu tests/test_paddle_real_smoke.py \
    --no-cov -q --junitxml="$evidence_dir/junit.xml"

python - "$evidence_dir/worker-result.json" <<'PY'
import json
from pathlib import Path
import sys

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
lines = document["lines"]
tokens = sum(len(line.get("tokens", [])) for line in lines)
print(f"real PP-OCRv4 smoke passed: {len(lines)} lines, {tokens} native word boxes")
PY
