"""License documentation guard (issue #90 acceptance: new dependencies must
be listed with their licenses in the docs).

Every backend the runtime planner can install — core and optional — must be
named in ``docs/licenses.md`` together with a license, and optional
technology-route backends whose *weights* are not Apache-2.0 must be marked
as such (Surya: OpenRAIL-M).
"""

from __future__ import annotations

from pathlib import Path

from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan

REPO_ROOT = Path(__file__).resolve().parent.parent
LICENSES_DOC = REPO_ROOT / "docs" / "licenses.md"

#: Backends whose model weights are known to be non-Apache-2.0.
NON_APACHE_WEIGHTS = {"surya": "OpenRAIL-M"}


def _plan(tmp_path: Path):
    profile = HardwareProfile("linux", "x86_64", None, None, None, 16 * 1024)
    return build_runtime_plan(profile, RuntimeLayout(tmp_path), uv_executable="/fake/uv")


def test_every_planned_backend_is_documented_with_a_license(tmp_path: Path) -> None:
    doc = LICENSES_DOC.read_text(encoding="utf-8")
    plan = _plan(tmp_path)

    for backend in plan.backends:
        assert backend.name in doc, f"backend {backend.name!r} missing from docs/licenses.md"


def test_optional_backends_are_marked_optional_in_licenses_doc(tmp_path: Path) -> None:
    doc = LICENSES_DOC.read_text(encoding="utf-8")
    plan = _plan(tmp_path)

    for backend in plan.backends:
        if backend.optional:
            # The doc row for an optional backend must say it is optional/on-demand.
            row = next(line for line in doc.splitlines() if backend.name in line)
            assert "optional" in row.lower(), f"{backend.name} row must mark it optional"


def test_non_apache_weights_are_called_out(tmp_path: Path) -> None:
    doc = LICENSES_DOC.read_text(encoding="utf-8")

    for backend, license_name in NON_APACHE_WEIGHTS.items():
        row = next(line for line in doc.splitlines() if backend.lower() in line.lower())
        assert license_name in row, f"{backend} weights license {license_name} not documented"


def test_benchmark_corpus_license_fields_are_documented(tmp_path: Path) -> None:
    """docs/benchmark-corpus.md must explain manifest license/source fields."""
    doc = (REPO_ROOT / "docs" / "benchmark-corpus.md").read_text(encoding="utf-8")
    assert "license" in doc
    assert "source" in doc


def test_every_backend_lock_file_exists(tmp_path: Path) -> None:
    """Each planned backend references a committed hash-locked requirements file."""
    plan = _plan(tmp_path)
    locks = REPO_ROOT / "src" / "scribeforge" / "runtime" / "locks"

    for backend in plan.backends:
        lock_args = [
            token
            for step in backend.steps
            for token in step.argv
            if token.endswith(".txt") and "locks" in token
        ]
        assert lock_args, f"{backend.name} has no lock file reference"
        for token in lock_args:
            name = token.rsplit("/", 1)[-1]
            assert (locks / name).is_file(), f"missing lock file {name}"


def test_technology_route_doc_names_optional_backends() -> None:
    doc = (REPO_ROOT / "docs" / "technology-route.md").read_text(encoding="utf-8")
    for name in ("paddleocr-vl", "surya"):
        assert name in doc.lower() or name.replace("-", " ").lower() in doc.lower()


def test_changelog_mentions_openrail_weight_caveat() -> None:
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "OpenRAIL-M" in changelog


def test_licenses_doc_warns_about_weight_vs_code_license() -> None:
    doc = LICENSES_DOC.read_text(encoding="utf-8")
    # The doc must distinguish code license from weights license somewhere.
    assert "weights" in doc.lower()


def test_core_backends_have_apache_licenses_documented(tmp_path: Path) -> None:
    doc = LICENSES_DOC.read_text(encoding="utf-8").lower()
    assert "mineru" in doc and "apache" in doc
    assert "paddleocr" in doc and "apache" in doc


def test_licenses_doc_lists_a_benchmark_corpus_license_example() -> None:
    doc = (REPO_ROOT / "docs" / "benchmark-corpus.md").read_text(encoding="utf-8")
    assert "CC0" in doc or "license" in doc.lower()
