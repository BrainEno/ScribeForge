from pathlib import Path


def test_core_failures_use_the_existing_junit_annotation_gate() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(
        encoding="utf-8"
    )

    core = workflow.split("  python-314:", 1)[0]
    assert "--junitxml=core-tests.xml" in core
    assert "--maxfail=1" in core
    assert "report_pytest_failure.py core-tests.xml" in core
    assert "if: failure()" in core
