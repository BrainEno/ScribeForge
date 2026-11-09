"""Run locked production setup and real PP-OCRv4 protocol smoke on Windows."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__:
    from scripts.create_paddle_smoke_image import create_smoke_image
    from scripts.record_paddle_smoke_evidence import record_evidence
else:
    from create_paddle_smoke_image import create_smoke_image
    from record_paddle_smoke_evidence import record_evidence

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
ImageCreator = Callable[[Path], Path]
EvidenceRecorder = Callable[..., dict[str, Any]]


def _append_log(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(text)
        if text and not text.endswith("\n"):
            stream.write("\n")


def _run_command(
    command: list[str],
    *,
    repository_root: Path,
    runner_log: Path,
    runner: CommandRunner,
    environment: dict[str, str] | None = None,
    stdout_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    _append_log(runner_log, f"$ {shlex.join(command)}\n")
    completed = runner(
        command,
        check=False,
        capture_output=True,
        text=True,
        cwd=repository_root,
        env=environment,
    )
    if completed.stdout:
        sys.stdout.write(completed.stdout)
        _append_log(runner_log, completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
        _append_log(runner_log, completed.stderr)
    if stdout_path is not None:
        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def run_windows_smoke(
    runtime_root: Path,
    evidence_dir: Path,
    repository_root: Path,
    *,
    command_runner: CommandRunner = subprocess.run,
    image_creator: ImageCreator = create_smoke_image,
    evidence_recorder: EvidenceRecorder = record_evidence,
) -> dict[str, Any]:
    """Run locked production setup and real PP-OCRv4 protocol smoke on Windows."""

    if runtime_root.exists() and not runtime_root.is_dir():
        raise RuntimeError(f"runtime root exists and is not a directory: {runtime_root}")
    if runtime_root.is_dir() and any(runtime_root.iterdir()):
        raise RuntimeError(f"refusing to modify a non-empty runtime root: {runtime_root}")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)
    runner_log = evidence_dir / "runner.log"

    smoke_image = image_creator(evidence_dir / "synthetic-paddle-smoke.png")
    _append_log(runner_log, f"synthetic smoke image: {smoke_image}\n")
    _run_command(
        [
            "scribeforge",
            "setup",
            "--json",
            "--backend",
            "paddleocr",
            "--runtime-root",
            str(runtime_root),
        ],
        repository_root=repository_root,
        runner_log=runner_log,
        runner=command_runner,
        stdout_path=evidence_dir / "setup.jsonl",
    )
    evidence_recorder(
        evidence_dir,
        runtime_root,
        repository_root,
        os_name="windows",
    )
    _append_log(runner_log, "recorded Windows model and runtime evidence\n")

    environment = os.environ.copy()
    environment.update(
        {
            "SCRIBEFORGE_RUN_PADDLE_SMOKE": "1",
            "SCRIBEFORGE_PADDLE_SMOKE_IMAGE": str(smoke_image),
            "SCRIBEFORGE_PADDLE_SMOKE_REPORT": str(evidence_dir / "worker-result.json"),
        }
    )
    _run_command(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "gpu",
            "tests/test_paddle_real_smoke.py",
            "--no-cov",
            "-q",
            f"--junitxml={evidence_dir / 'junit.xml'}",
        ],
        repository_root=repository_root,
        runner_log=runner_log,
        runner=command_runner,
        environment=environment,
    )
    document = json.loads((evidence_dir / "worker-result.json").read_text(encoding="utf-8"))
    lines = document["lines"]
    tokens = sum(len(line.get("tokens", [])) for line in lines)
    message = (
        f"real PP-OCRv4 Windows smoke passed: {len(lines)} lines, {tokens} native word boxes\n"
    )
    sys.stdout.write(message)
    _append_log(runner_log, message)
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=os.environ.get("SCRIBEFORGE_RUNTIME_ROOT"),
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path(
            os.environ.get(
                "SCRIBEFORGE_PADDLE_SMOKE_EVIDENCE",
                str(Path(tempfile.gettempdir()) / "scribeforge-paddle-real-evidence"),
            )
        ),
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()
    if args.runtime_root is None:
        parser.error("set --runtime-root or SCRIBEFORGE_RUNTIME_ROOT")
    run_windows_smoke(args.runtime_root, args.evidence_dir, args.repository_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
