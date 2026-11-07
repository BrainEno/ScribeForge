import json
import subprocess
import sys
from pathlib import Path


def test_windows_real_smoke_workflow_runs_and_uploads_windows_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow_path = root / ".github/workflows/paddle-real-smoke-windows.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "runs-on: windows-latest" in workflow
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert "python scripts/run_paddle_real_smoke_windows.py" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "paddle-real-smoke-windows-" in workflow
    assert "if: always()" in workflow

    linux_workflow = (root / ".github/workflows/paddle-real-smoke.yml").read_text(
        encoding="utf-8"
    )
    for workflow_text in (workflow, linux_workflow):
        assert "scripts/create_paddle_smoke_image.py" in workflow_text
        assert "scripts/record_paddle_smoke_evidence.py" in workflow_text


def test_summary_labels_the_platform_from_real_evidence(tmp_path: Path) -> None:
    (tmp_path / "model-evidence.json").write_text(
        json.dumps(
            {
                "platform": "windows",
                "installed_distributions": {"python": "3.12.16"},
                "lock_file": {"sha256": "lock"},
                "models": [],
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

    assert "Real Paddle smoke (windows)" in output.read_text(encoding="utf-8")
