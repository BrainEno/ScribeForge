from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.run_paddle_real_smoke_windows as smoke_runner


def test_windows_runner_executes_production_setup_and_real_smoke_command(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    evidence = tmp_path / "evidence"
    repository = tmp_path / "repository"
    commands: list[tuple[list[str], dict[str, object]]] = []

    def fake_image(output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"synthetic")
        return output

    def fake_evidence(
        evidence_dir: Path,
        runtime_root: Path,
        repository_root: Path,
        *,
        os_name: str,
    ) -> dict[str, object]:
        assert evidence_dir == evidence
        assert runtime_root == runtime
        assert repository_root == repository
        assert os_name == "windows"
        return {"platform": os_name}

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append((argv, kwargs))
        if argv[:3] == ["scribeforge", "setup", "--json"]:
            return subprocess.CompletedProcess(argv, 0, stdout='{"success": true}\n', stderr="")
        assert argv[:3] == [sys.executable, "-m", "pytest"]
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        report = Path(environment["SCRIBEFORGE_PADDLE_SMOKE_REPORT"])
        report.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "lines": [{"tokens": [{"text": "a"}, {"text": "b"}]}],
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="1 passed\n", stderr="")

    document = smoke_runner.run_windows_smoke(
        runtime,
        evidence,
        repository,
        command_runner=fake_run,
        image_creator=fake_image,
        evidence_recorder=fake_evidence,
    )

    assert len(commands) == 2
    setup, setup_kwargs = commands[0]
    assert setup[:5] == ["scribeforge", "setup", "--json", "--backend", "paddleocr"]
    assert setup[setup.index("--runtime-root") + 1] == str(runtime)
    assert setup_kwargs["check"] is False
    assert setup_kwargs["cwd"] == repository
    assert (evidence / "setup.jsonl").read_text() == '{"success": true}\n'

    pytest_command, pytest_kwargs = commands[1]
    assert "tests/test_paddle_real_smoke.py" in pytest_command
    assert f"--junitxml={evidence / 'junit.xml'}" in pytest_command
    environment = pytest_kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["SCRIBEFORGE_RUN_PADDLE_SMOKE"] == "1"
    assert environment["SCRIBEFORGE_PADDLE_SMOKE_IMAGE"] == str(
        evidence / "synthetic-paddle-smoke.png"
    )
    assert document["schema_version"] == 2
    log = (evidence / "runner.log").read_text(encoding="utf-8")
    assert "scribeforge setup" in log
    assert "1 passed" in log


def test_windows_runner_refuses_to_modify_a_non_empty_runtime(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    marker = runtime / "user-data.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(RuntimeError, match="non-empty runtime root"):
        smoke_runner.run_windows_smoke(runtime, tmp_path / "evidence", tmp_path)

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_windows_runner_preserves_setup_failure_output(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv, 1, stdout="setup failed\n", stderr="real installer error\n"
        )

    with pytest.raises(subprocess.CalledProcessError):
        smoke_runner.run_windows_smoke(
            tmp_path / "runtime",
            evidence,
            tmp_path,
            command_runner=fake_run,
            image_creator=lambda output: output,
            evidence_recorder=lambda *args, **kwargs: {},
        )

    log = (evidence / "runner.log").read_text(encoding="utf-8")
    assert "setup failed" in log
    assert "real installer error" in log
    assert (evidence / "setup.jsonl").read_text() == "setup failed\n"
