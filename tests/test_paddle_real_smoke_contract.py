import json
import os
import subprocess
import sys
from pathlib import Path


def test_real_smoke_uses_managed_worker_python_and_can_record_evidence() -> None:
    source = (Path(__file__).parent / "test_paddle_real_smoke.py").read_text(encoding="utf-8")

    assert "worker_command(" in source
    assert "sys.executable" not in source
    assert "SCRIBEFORGE_PADDLE_SMOKE_REPORT" in source


def test_real_smoke_ci_runs_locked_production_setup_and_uploads_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "run_paddle_real_smoke.sh"
    evidence_script = root / "scripts" / "record_paddle_smoke_evidence.py"
    workflow = root / ".github" / "workflows" / "paddle-real-smoke.yml"

    assert script.is_file()
    if os.name != "nt":
        subprocess.run(["bash", "-n", script.as_posix()], check=True)
    script_text = script.read_text(encoding="utf-8")
    assert "setup --json --backend paddleocr" in script_text
    assert "SCRIBEFORGE_RUN_PADDLE_SMOKE=1" in script_text
    assert "tests/test_paddle_real_smoke.py" in script_text
    assert "record_paddle_smoke_evidence.py" in script_text
    assert "runner.log" in script_text
    evidence_text = evidence_script.read_text(encoding="utf-8")
    assert "model-evidence.json" in evidence_text
    assert "paddleocr-cpu-py312-universal.txt" in evidence_text
    assert "paddle_model_root(layout)" in evidence_text
    assert "model.weight_path" in evidence_text
    assert "model.weight_filename" not in evidence_text
    assert "layout.paddle_models_dir" not in evidence_text

    workflow_text = workflow.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow_text
    assert "pull_request:" in workflow_text
    assert "scripts/run_paddle_real_smoke.sh" in workflow_text
    assert "actions/upload-artifact@v4" in workflow_text
    assert "if: always()" in workflow_text


def test_smoke_summary_reports_real_counts_without_accuracy_claim(tmp_path: Path) -> None:
    (tmp_path / "model-evidence.json").write_text(
        json.dumps(
            {
                "installed_distributions": {
                    "python": "3.12.16",
                    "paddlepaddle": "3.3.0",
                    "paddleocr": "3.4.2",
                },
                "lock_file": {"sha256": "lock-digest"},
                "models": [{"name": "det", "revision": "det-revision"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "worker-result.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "lines": [{"tokens": [{"text": "a"}, {"text": "b"}]}, {"tokens": []}],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "summary.md"

    subprocess.run(
        [
            sys.executable,
            "scripts/paddle_smoke_summary.py",
            str(tmp_path),
            "success",
            str(output),
        ],
        check=True,
    )

    summary = output.read_text(encoding="utf-8")
    assert "status: success" in summary
    assert "2 native word boxes" in summary
    assert "3.12.16" in summary
    assert "det-revision" in summary
    assert "accuracy was not evaluated" in summary
    assert "recognition accuracy passed" not in summary


def test_smoke_summary_preserves_failure_diagnostics(tmp_path: Path) -> None:
    (tmp_path / "setup.jsonl").write_text("first line\nreal installer error\n", encoding="utf-8")
    output = tmp_path / "summary.md"

    subprocess.run(
        [
            sys.executable,
            "scripts/paddle_smoke_summary.py",
            str(tmp_path),
            "failure",
            str(output),
        ],
        check=True,
    )

    summary = output.read_text(encoding="utf-8")
    assert "status: failure" in summary
    assert "real installer error" in summary
    assert "real PP-OCRv4 smoke passed" not in summary
    assert "accuracy was not evaluated" in summary


def test_smoke_summary_prefers_full_runner_failure_log(tmp_path: Path) -> None:
    (tmp_path / "setup.jsonl").write_text('{"success": true}\n', encoding="utf-8")
    (tmp_path / "runner.log").write_text(
        "setup ready\nAttributeError: weight_filename\n", encoding="utf-8"
    )
    output = tmp_path / "summary.md"

    subprocess.run(
        [
            sys.executable,
            "scripts/paddle_smoke_summary.py",
            str(tmp_path),
            "failure",
            str(output),
        ],
        check=True,
    )

    summary = output.read_text(encoding="utf-8")
    assert "AttributeError: weight_filename" in summary
    assert "setup ready" in summary
