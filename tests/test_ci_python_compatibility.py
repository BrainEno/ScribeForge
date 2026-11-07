from pathlib import Path


def test_python_314_uses_an_ubuntu_runner_with_the_requested_toolcache() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    # ubuntu-latest moved to 26.04 before actions/python-versions provided 3.14.4
    # for that image; keep the compatibility matrix on the available LTS image.
    assert "os: [ubuntu-24.04, windows-latest]" in workflow
    assert "os: [ubuntu-latest, windows-latest]" not in workflow
