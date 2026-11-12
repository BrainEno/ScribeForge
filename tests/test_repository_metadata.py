"""Repository-level metadata checks: license, changelog, README (#67)."""

from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_apache_license_file_exists_with_full_text() -> None:
    license_path = REPO_ROOT / "LICENSE"
    assert license_path.is_file(), "repository root must ship a LICENSE file"
    text = license_path.read_text(encoding="utf-8")
    assert "Apache License" in text
    assert "Version 2.0, January 2004" in text
    assert "APPENDIX: How to apply the Apache License to your work" in text


def test_pyproject_declares_apache_2_license() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    project = pyproject["project"]
    assert project["license"] == "Apache-2.0"
    license_files = project.get("license-files", [])
    assert "LICENSE" in license_files


def test_changelog_documents_the_current_version() -> None:
    changelog = REPO_ROOT / "CHANGELOG.md"
    assert changelog.is_file(), "a CHANGELOG.md must anchor released versions"
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    version = pyproject["project"]["version"]
    assert version in changelog.read_text(encoding="utf-8")


def test_contributing_references_the_agent_rules() -> None:
    contributing = REPO_ROOT / "CONTRIBUTING.md"
    assert contributing.is_file()
    text = contributing.read_text(encoding="utf-8")
    assert "AGENTS.md" in text
    assert "Apache" in text


def test_agents_dod_requires_failure_path_coverage() -> None:
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "failure paths" in agents, "DoD must require failure-path coverage"


def test_ci_enforces_per_module_coverage_floors() -> None:
    ci = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "fail-under" in ci
    assert "project_backup.py" in ci and "runtime/probe.py" in ci


def test_license_inventory_documents_engine_licenses() -> None:
    inventory = REPO_ROOT / "docs" / "licenses.md"
    assert inventory.is_file(), "docs/licenses.md must inventory dependency licenses"
    text = inventory.read_text(encoding="utf-8")
    # The project itself.
    assert "Apache-2.0" in text
    # Engines pulled in at runtime by the planner.
    assert "MinerU" in text and "PaddleOCR" in text
    # Surya weights are OpenRAIL-M, not Apache-2.0 — must be explicit (issue #90).
    assert "OpenRAIL-M" in text and "Surya" in text
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "licenses.md" in changelog


def test_readme_points_to_the_license() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Apache License 2.0" in readme or "Apache-2.0" in readme
    assert "[LICENSE](LICENSE)" in readme
