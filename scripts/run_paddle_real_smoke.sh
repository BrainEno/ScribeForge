#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

: "${SCRIBEFORGE_RUNTIME_ROOT:?set SCRIBEFORGE_RUNTIME_ROOT to a new empty runtime directory}"
evidence_dir="${SCRIBEFORGE_PADDLE_SMOKE_EVIDENCE:-${TMPDIR:-/tmp}/scribeforge-paddle-smoke-evidence}"
mkdir -p "$evidence_dir"
evidence_dir=$(cd "$evidence_dir" && pwd)
export SCRIBEFORGE_PADDLE_SMOKE_EVIDENCE="$evidence_dir"
runner_log="$evidence_dir/runner.log"
exec > >(tee "$runner_log") 2>&1

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
python scripts/create_paddle_smoke_image.py "$smoke_image"

scribeforge setup --json --backend paddleocr \
    --runtime-root "$SCRIBEFORGE_RUNTIME_ROOT" \
    | tee "$evidence_dir/setup.jsonl"

python scripts/record_paddle_smoke_evidence.py \
    "$evidence_dir" "$SCRIBEFORGE_RUNTIME_ROOT" "$repo_root"

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
