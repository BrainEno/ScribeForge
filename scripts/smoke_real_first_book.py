"""Run real dual OCR and EPUB export through an installed Windows backend.

The only reference is the text printed in the synthetic input. It is never
injected as OCR or accepted text. When review is necessary, select an actual
engine candidate only if it matches unambiguous text on the sample page.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__:
    from scripts.create_paddle_smoke_image import SMOKE_LINES, create_smoke_image
    from scripts.smoke_packaged_epub import read_epub_spine
else:
    from create_paddle_smoke_image import SMOKE_LINES, create_smoke_image
    from smoke_packaged_epub import read_epub_spine

BACKENDS = ("mineru", "paddleocr")


def _spacing(text: str) -> str:
    return " ".join(text.split())


def choose_candidate(item: dict[str, Any]) -> str:
    """Smoke-only policy: never supply a reference reading absent from OCR."""
    candidates: dict[str, str] = {}
    for key in ("primary_text", "secondary_text"):
        text = item.get(key)
        if not isinstance(text, str):
            continue
        normalized = _spacing(text)
        if normalized and any(
            re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", line) for line in SMOKE_LINES
        ):
            candidates.setdefault(normalized, text)
    if len(candidates) != 1:
        raise RuntimeError(
            f"Cannot select a unique observed reading for page {item.get('page_index')} "
            f"pair {item.get('pair_index')}; inspect the review queue evidence."
        )
    return next(iter(candidates.values()))


def _check_text(text: str) -> None:
    normalized = _spacing(text)
    previous_end = 0
    for line in SMOKE_LINES:
        if normalized.count(line) != 1:
            raise RuntimeError(f"Expected exactly one occurrence in EPUB spine: {line!r}")
        index = normalized.find(line, previous_end)
        if index < 0:
            raise RuntimeError("EPUB sample text is out of reading order")
        previous_end = index + len(line)


def _read_json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise RuntimeError(f"Unsupported JSON response: {path.name}")
    return result


def _one_project(path: Path) -> dict[str, Any]:
    projects = _read_json(path).get("projects")
    if not isinstance(projects, list) or len(projects) != 1 or not isinstance(projects[0], dict):
        raise RuntimeError(f"Expected one project in {path.name}")
    return projects[0]


def _environment(root: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "UV_", "HF_", "PIP_", "SCRIBEFORGE_"))
        and key.upper() not in {"VIRTUAL_ENV", "CONDA_PREFIX", "PATH"}
    }
    env.update(
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        UV_CACHE_DIR=str(root / "uv-cache"),
        HF_HOME=str(root / "hf-cache"),
        APPDATA=str(root / "appdata"),
        LOCALAPPDATA=str(root / "local-appdata"),
        PATH=str(Path(os.environ["SystemRoot"]) / "System32") if os.name == "nt" else os.defpath,
    )
    for key in ("APPDATA", "LOCALAPPDATA"):
        Path(env[key]).mkdir()
    return env


class Backend:
    def __init__(self, executable: Path, evidence: Path, environment: dict[str, str]) -> None:
        self.executable = executable
        self.evidence = evidence
        self.environment = environment

    def run(self, step: str, *args: str, timeout: int = 90) -> Path:
        stdout_path = self.evidence / f"{step}.stdout"
        stderr_path = self.evidence / f"{step}.stderr"
        print(f"Starting {step}: {args[0]}", flush=True)
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                result = subprocess.run(
                    [str(self.executable), *args],
                    cwd=self.executable.parent,
                    env=self.environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{step} timed out after {timeout} seconds; see saved logs") from exc
        if result.returncode != 0:
            with stderr_path.open("rb") as log:
                log.seek(max(0, stderr_path.stat().st_size - 1600))
                tail = log.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"{step} exited {result.returncode}; {tail or 'see saved logs'}")
        return stdout_path


def acceptance(
    executable: Path,
    root: Path,
    evidence: Path,
    *,
    image_creator: Callable[[Path], Path] = create_smoke_image,
) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    backend = Backend(executable, evidence, _environment(root))
    runtime = f"--runtime-root={root / 'runtime'}"
    store = f"--store={root / '项目 Store'}"
    source = image_creator(evidence / "扫描样页.png")
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output = evidence / "成品 Book.epub"
    backend.run("01-setup", "setup", "--json", runtime, timeout=4200)
    state = _read_json(backend.run("02-runtime", "runtime-status", "--json", runtime))
    for name in BACKENDS:
        backend_state = state.get("backends", {}).get(name, {})
        if (
            backend_state.get("state") != "ready"
            or backend_state.get("environment_exists") is not True
        ):
            raise RuntimeError(f"{name} is not ready after fresh setup")
    backend.run("03-process", "process", str(source), store, runtime, "--workers=1", timeout=1500)
    project = _one_project(backend.run("04-status", "status", store, "--json"))
    if (
        project.get("pages_total") != 1
        or project.get("pages_with_both_ocr_success") != 1
        or project.get("source_sha256") != source_digest
    ):
        raise RuntimeError("The installed pipeline did not recognize the source with both engines")
    queue_args = ("review", store, "--json", "--include-page-furniture")
    queue = _one_project(backend.run("05-review", *queue_args))["items"]
    if not isinstance(queue, list) or len(queue) > 30:
        raise RuntimeError("Unexpected review queue for the one-page fixture")
    for number, item in enumerate(queue):
        candidate = choose_candidate(item)
        token = item.get("review_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Review queue item has no evidence token")
        backend.run(
            f"06-resolve-{number:02}",
            "resolve",
            store,
            f"--page={item['page_index']}",
            f"--pair={item['pair_index']}",
            f"--review-token={token}",
            f"--text={candidate}",
        )
    remaining = _one_project(backend.run("07-review-after", *queue_args))["items"]
    if remaining != []:
        raise RuntimeError("An unresolved OCR pair remains after fixture review")
    backend.run(
        "08-export", "export", store, f"--out={output}", "--language=en", "--include-page-furniture"
    )
    _check_text(read_epub_spine(output))
    report = _read_json(
        backend.run(
            "09-verify", "verify-export", f"--file={output}", f"--source-sha256={source_digest}"
        )
    )
    if report.get("status") != "consistent" or report.get("project_source_matches") is not True:
        raise RuntimeError(
            "The installed backend could not verify the exported EPUB against the source"
        )
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_digest:
        raise RuntimeError("The source scan changed during acceptance")
    result = {
        "schema_version": 1,
        "scope": "installed-backend/synthetic-single-page/real-models",
        "engines": list(BACKENDS),
        "pages": 1,
        "reviewed_pairs": len(queue),
        "source_sha256": source_digest,
        "epub_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    (evidence / "passed.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = acceptance(
            args.backend.resolve(strict=True), args.root.resolve(), args.evidence.resolve()
        )
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error title=Real first-book acceptance::{message}", flush=True)
        else:
            print(f"Real first-book acceptance failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
