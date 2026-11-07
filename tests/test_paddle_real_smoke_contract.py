import subprocess
from pathlib import Path


def test_real_smoke_uses_managed_worker_python_and_can_record_evidence() -> None:
    source = (Path(__file__).parent / "test_paddle_real_smoke.py").read_text(encoding="utf-8")

    assert "worker_command(" in source
    assert "sys.executable" not in source
    assert "SCRIBEFORGE_PADDLE_SMOKE_REPORT" in source


def test_real_smoke_ci_runs_locked_production_setup_and_uploads_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "run_paddle_real_smoke.sh"
    workflow = root / ".github" / "workflows" / "paddle-real-smoke.yml"

    assert script.is_file()
    subprocess.run(["bash", "-n", script], check=True)
    script_text = script.read_text(encoding="utf-8")
    assert "setup --json --backend paddleocr" in script_text
    assert "SCRIBEFORGE_RUN_PADDLE_SMOKE=1" in script_text
    assert "tests/test_paddle_real_smoke.py" in script_text
    assert "model-evidence.json" in script_text
    assert "paddleocr-cpu-py312-universal.txt" in script_text

    workflow_text = workflow.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow_text
    assert "pull_request:" in workflow_text
    assert "scripts/run_paddle_real_smoke.sh" in workflow_text
    assert "actions/upload-artifact@v4" in workflow_text
    assert "if: always()" in workflow_text
