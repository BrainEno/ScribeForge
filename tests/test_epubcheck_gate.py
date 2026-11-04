"""EPUBCheck CI gate must actually run both positive and negative controls."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "epubcheck_gate.py"


def _module():
    spec = importlib.util.spec_from_file_location("epubcheck_gate", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_validator(tmp_path: Path, program: str) -> list[str]:
    script = tmp_path / "validator.py"
    script.write_text(program, encoding="utf-8")
    return [sys.executable, str(script)]


def test_gate_accepts_valid_and_observes_official_error_for_invalid(tmp_path: Path) -> None:
    gate = _module()
    valid, invalid = gate.create_fixtures(tmp_path)
    code = """import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    valid = 'mimetype' in archive.namelist()
if not valid:
    print('ERROR(PKG-006): mimetype missing')
sys.exit(0 if valid else 1)
"""
    gate.run_gate(_fake_validator(tmp_path, code), valid, invalid)


def test_gate_propagates_rejected_valid_file_and_unexpected_negative_pass(tmp_path: Path) -> None:
    gate = _module()
    valid, invalid = gate.create_fixtures(tmp_path)
    command = _fake_validator(tmp_path, "import sys; print('ERROR(PKG-006): bad'); sys.exit(1)")
    with pytest.raises(RuntimeError, match="valid EPUB"):
        gate.run_gate(command, valid, invalid)
    command = _fake_validator(tmp_path, "import sys; sys.exit(0)")
    with pytest.raises(RuntimeError, match="invalid EPUB"):
        gate.run_gate(command, valid, invalid)


def test_gate_does_not_mistake_java_or_validator_failure_for_negative_control(
    tmp_path: Path,
) -> None:
    gate = _module()
    valid, invalid = gate.create_fixtures(tmp_path)
    code = """import sys
if sys.argv[1].endswith('invalid.epub'):
    print('Error: Unable to access jarfile', file=sys.stderr)
    sys.exit(1)
sys.exit(0)
"""
    with pytest.raises(RuntimeError, match="official EPUBCheck error"):
        gate.run_gate(_fake_validator(tmp_path, code), valid, invalid)


def test_workflow_does_not_skip_or_swallow_official_validator() -> None:
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text()
    assert "epubcheck_gate.py" in workflow
    assert "epubcheck-5.4.0.zip" in workflow
    assert "continue-on-error: true" not in workflow
    assert "java -jar" not in workflow or "|| true" not in workflow


def test_fixture_is_deterministic_and_negative_differs_only_by_mimetype(tmp_path: Path) -> None:
    import zipfile

    gate = _module()
    valid, invalid = gate.create_fixtures(tmp_path / "first")
    valid_again, invalid_again = gate.create_fixtures(tmp_path / "second")
    assert valid.read_bytes() == valid_again.read_bytes()
    assert invalid.read_bytes() == invalid_again.read_bytes()
    with zipfile.ZipFile(valid) as positive, zipfile.ZipFile(invalid) as negative:
        assert positive.namelist()[0] == "mimetype"
        assert set(positive.namelist()) - set(negative.namelist()) == {"mimetype"}
        for name in negative.namelist():
            assert negative.read(name) == positive.read(name)


def test_fixture_exercises_cover_image_and_chapter_navigation(tmp_path: Path) -> None:
    import zipfile

    gate = _module()
    valid, _ = gate.create_fixtures(tmp_path)
    with zipfile.ZipFile(valid) as archive:
        package = archive.read("OEBPS/content.opf")
        assert b'properties="cover-image"' in package
        assert any(name.startswith("OEBPS/images/") for name in archive.namelist())
        assert "第一章" in archive.read("OEBPS/nav.xhtml").decode("utf-8")
