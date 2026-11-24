"""Release gate must validate versions, CI provenance, checksums and notes (#220/#245)."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release_gate.py"

_VERSION = "0.1.1"


def _module():
    spec = importlib.util.spec_from_file_location("release_gate", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_repo(root: Path, *, version: str = _VERSION, skip: set[str] | None = None) -> Path:
    """Create a minimal repository layout with consistent version metadata."""
    skip = skip or set()
    if "pyproject" not in skip:
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "scribeforge"\nversion = "{version}"\n',
            encoding="utf-8",
        )
    if "init" not in skip:
        pkg = root / "src" / "scribeforge"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    if "iss" not in skip:
        iss_dir = root / "packaging" / "windows"
        iss_dir.mkdir(parents=True, exist_ok=True)
        (iss_dir / "ScribeForge.iss").write_text(
            f'#define MyAppVersion "{version}"\n', encoding="utf-8"
        )
    if "pubspec" not in skip:
        client = root / "clients" / "flutter_desktop"
        client.mkdir(parents=True, exist_ok=True)
        (client / "pubspec.yaml").write_text(
            f"name: scribeforge_desktop\nversion: {version}+2\n", encoding="utf-8"
        )
    if "readme" not in skip:
        (root / "README.md").write_text(f"# ScribeForge\n\ncurrent: v{version}\n", encoding="utf-8")
    if "changelog" not in skip:
        (root / "CHANGELOG.md").write_text(
            "## [Unreleased]\n\n"
            f"## [{version}] - 2026-11-20\n\n"
            "### Fixed\n\n- Test entry.\n\n"
            "## [0.1.0] - 2026-11-11\n\n- Old.\n",
            encoding="utf-8",
        )
    return root


def _check_run(
    name: str,
    *,
    run_id: int = 1,
    status: str = "completed",
    conclusion: str | None = "success",
) -> dict:
    return {
        "id": run_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "details_url": f"https://github.com/BrainEno/ScribeForge/actions/runs/{run_id}",
    }


# --------------------------------------------------------------------------- #
# check: version consistency
# --------------------------------------------------------------------------- #


def test_check_accepts_consistent_repo(tmp_path: Path) -> None:
    gate = _module()
    _write_repo(tmp_path)
    assert gate.check_versions(tmp_path, _VERSION) == []


def test_check_normalizes_v_prefix_and_rejects_bad_tag() -> None:
    gate = _module()
    assert gate.normalize_version("v0.1.1") == "0.1.1"
    with pytest.raises(gate.GateError, match="版本格式无效"):
        gate.normalize_version("0.1.1-rc1")
    with pytest.raises(gate.GateError, match="版本格式无效"):
        gate.normalize_version("v1.2")


def test_check_reports_each_mismatched_source(tmp_path: Path) -> None:
    gate = _module()
    _write_repo(tmp_path, version="0.1.0")
    errors = gate.check_versions(tmp_path, _VERSION)
    joined = "\n".join(errors)
    assert "pyproject.toml" in joined
    assert "__init__.py" in joined
    assert "ScribeForge.iss" in joined
    assert "pubspec.yaml" in joined
    assert "CHANGELOG.md" in joined
    assert "README.md" in joined
    assert all(_VERSION in error for error in errors)


def test_check_flags_missing_changelog_section(tmp_path: Path) -> None:
    gate = _module()
    _write_repo(tmp_path, skip={"changelog"})
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [0.1.0] - 2026-11-11\n\n- Old.\n", encoding="utf-8"
    )
    errors = gate.check_versions(tmp_path, _VERSION)
    assert any("CHANGELOG.md" in error for error in errors)


def test_check_flags_empty_changelog_section(tmp_path: Path) -> None:
    gate = _module()
    (tmp_path / "CHANGELOG.md").write_text(
        f"## [{_VERSION}] - 2026-11-20\n\n## [0.1.0] - 2026-11-11\n\n- Old.\n",
        encoding="utf-8",
    )
    with pytest.raises(gate.GateError, match="为空"):
        gate.changelog_section(tmp_path, _VERSION)


def test_changelog_section_returns_body_without_adjacent_versions(tmp_path: Path) -> None:
    gate = _module()
    _write_repo(tmp_path)
    body = gate.changelog_section(tmp_path, _VERSION)
    assert "Test entry." in body
    assert "0.1.0" not in body


def test_cli_check_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    gate = _module()
    _write_repo(tmp_path)
    assert gate.main(["--root", str(tmp_path), "check", "--version", _VERSION]) == 0
    _write_repo(tmp_path, version="0.1.0")
    assert gate.main(["--root", str(tmp_path), "check", "--version", _VERSION]) == 1
    captured = capsys.readouterr()
    assert "::error title=Release gate::" in captured.err


# --------------------------------------------------------------------------- #
# checks: CI provenance
# --------------------------------------------------------------------------- #


def _payload(*runs: dict) -> dict:
    return {"check_runs": list(runs)}


def test_checks_accepts_green_main(tmp_path: Path) -> None:
    gate = _module()
    payload = _payload(
        _check_run("core", run_id=1),
        _check_run("flutter-desktop", run_id=2),
        _check_run("flutter-desktop-windows", run_id=3),
        _check_run("package", run_id=4),
        _check_run("Python 3.14.4 core compatibility (ubuntu-24.04)", run_id=5),
        _check_run("Python 3.14.4 core compatibility (windows-latest)", run_id=6),
        # Unrelated checks must not block the release.
        _check_run("stale", run_id=7, conclusion="stale"),
    )
    path = tmp_path / "runs.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert gate.load_and_evaluate_check_runs(path) == []


def test_checks_requires_each_named_check(tmp_path: Path) -> None:
    gate = _module()
    for missing in ("core", "flutter-desktop", "flutter-desktop-windows"):
        payload = _payload(
            *[
                _check_run(name)
                for name in ("core", "flutter-desktop", "flutter-desktop-windows")
                if name != missing
            ]
        )
        errors = gate.evaluate_check_runs(payload)
        assert any(missing in error and "没有" in error for error in errors)


def test_checks_rejects_failed_required_check(tmp_path: Path) -> None:
    gate = _module()
    payload = _payload(
        _check_run("core", run_id=1, conclusion="failure"),
        _check_run("flutter-desktop", run_id=2),
        _check_run("flutter-desktop-windows", run_id=3),
    )
    errors = gate.evaluate_check_runs(payload)
    assert any("core" in error and "failure" in error for error in errors)


def test_checks_rejects_pending_required_check(tmp_path: Path) -> None:
    gate = _module()
    payload = _payload(
        _check_run("core", run_id=1, status="in_progress", conclusion=None),
        _check_run("flutter-desktop", run_id=2),
        _check_run("flutter-desktop-windows", run_id=3),
    )
    errors = gate.evaluate_check_runs(payload)
    assert any("core" in error and "in_progress" in error for error in errors)


def test_checks_treats_package_and_python_as_conditional(tmp_path: Path) -> None:
    gate = _module()
    base = (
        _check_run("core"),
        _check_run("flutter-desktop"),
        _check_run("flutter-desktop-windows"),
    )
    # Absent: OK.
    assert gate.evaluate_check_runs(_payload(*base)) == []
    # Failed: blocked.
    for name in ("package", "Python 3.14.4 core compatibility (ubuntu-24.04)"):
        errors = gate.evaluate_check_runs(
            _payload(*base, _check_run(name, run_id=9, conclusion="failure"))
        )
        assert any(name in error for error in errors)


def test_checks_uses_latest_attempt_per_name(tmp_path: Path) -> None:
    gate = _module()
    payload = _payload(
        _check_run("core", run_id=1, conclusion="failure"),
        _check_run("core", run_id=2, conclusion="success"),
        _check_run("flutter-desktop", run_id=3),
        _check_run("flutter-desktop-windows", run_id=4),
    )
    assert gate.evaluate_check_runs(payload) == []


def test_checks_rejects_malformed_payload(tmp_path: Path) -> None:
    gate = _module()
    with pytest.raises(gate.GateError, match="check_runs"):
        gate.evaluate_check_runs({})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(gate.GateError, match="无法读取"):
        gate.load_and_evaluate_check_runs(bad)


def test_cli_checks_exit_code(tmp_path: Path) -> None:
    gate = _module()
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            _payload(
                _check_run("core"),
                _check_run("flutter-desktop"),
                _check_run("flutter-desktop-windows"),
            )
        ),
        encoding="utf-8",
    )
    assert gate.main(["checks", "--file", str(good)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_payload(_check_run("core", conclusion="failure"))), encoding="utf-8")
    assert gate.main(["checks", "--file", str(bad)]) == 1


# --------------------------------------------------------------------------- #
# sums: verification and normalization
# --------------------------------------------------------------------------- #


def _make_dist(tmp_path: Path, *, break_one: bool = False) -> Path:
    dist = tmp_path / "dist"
    portable = dist / "portable"
    installer = dist / "installer"
    portable.mkdir(parents=True)
    installer.mkdir(parents=True)
    zip_bytes = b"zip-payload"
    exe_bytes = b"exe-payload"
    (portable / "ScribeForge-windows-x64.zip").write_bytes(zip_bytes)
    (installer / "ScribeForge-Setup-x64.exe").write_bytes(exe_bytes)
    zip_digest = hashlib.sha256(zip_bytes).hexdigest()
    exe_digest = hashlib.sha256(exe_bytes).hexdigest()
    if break_one:
        exe_digest = "0" * 64
    # Paths as recorded by the Windows packaging job (repository-relative).
    (dist / "SHA256SUMS.txt").write_text(
        f"{zip_digest}  dist/portable/ScribeForge-windows-x64.zip\n"
        f"{exe_digest}  dist/installer/ScribeForge-Setup-x64.exe\n",
        encoding="utf-8",
    )
    return dist


def test_sums_verifies_and_writes_bare_names(tmp_path: Path) -> None:
    gate = _module()
    dist = _make_dist(tmp_path)
    out = dist / "release" / "SHA256SUMS.txt"
    # verify uses paths relative to cwd; run from tmp_path like the workflow.
    import os

    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        errors = gate.verify_and_normalize_sums(dist, out)
    finally:
        os.chdir(previous)
    assert errors == []
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("  ScribeForge-windows-x64.zip")
    assert lines[1].endswith("  ScribeForge-Setup-x64.exe")
    assert all(len(line.split()[0]) == 64 for line in lines)


def test_sums_detects_corrupted_asset(tmp_path: Path) -> None:
    gate = _module()
    dist = _make_dist(tmp_path, break_one=True)
    out = dist / "release" / "SHA256SUMS.txt"
    import os

    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        errors = gate.verify_and_normalize_sums(dist, out)
    finally:
        os.chdir(previous)
    assert any("SHA-256 与打包记录不符" in error for error in errors)
    assert not out.exists()


def test_sums_reports_missing_asset(tmp_path: Path) -> None:
    gate = _module()
    dist = _make_dist(tmp_path)
    (dist / "portable" / "ScribeForge-windows-x64.zip").unlink()
    out = dist / "release" / "SHA256SUMS.txt"
    import os

    previous = Path.cwd()
    os.chdir(tmp_path)
    try:
        errors = gate.verify_and_normalize_sums(dist, out)
    finally:
        os.chdir(previous)
    assert any("文件不存在" in error for error in errors)


def test_sums_rejects_malformed_and_empty(tmp_path: Path) -> None:
    gate = _module()
    with pytest.raises(gate.GateError, match="行格式无效"):
        gate.parse_sums("zzzz  file\n")
    with pytest.raises(gate.GateError, match="为空"):
        gate.parse_sums("\n")


# --------------------------------------------------------------------------- #
# notes: rendering
# --------------------------------------------------------------------------- #


def test_notes_include_disclosure_checksums_and_changelog(tmp_path: Path) -> None:
    gate = _module()
    _write_repo(tmp_path)
    entries = [
        ("a" * 64, "dist/installer/ScribeForge-Setup-x64.exe"),
        ("b" * 64, "dist/portable/ScribeForge-windows-x64.zip"),
    ]
    text = gate.render_release_notes(
        version=_VERSION,
        tag=f"v{_VERSION}",
        sha="deadbeef",
        changelog_body=gate.changelog_section(tmp_path, _VERSION),
        sums_entries=entries,
        run_url="https://github.com/BrainEno/ScribeForge/actions/runs/42",
    )
    assert "未签名" in text
    assert "unsigned" in text
    assert "SmartScreen" in text
    assert "deadbeef" in text
    assert "runs/42" in text
    assert "a" * 64 in text
    assert "b" * 64 in text
    assert "ScribeForge-Setup-x64.exe" in text
    assert "Test entry." in text
    assert "sha256sum -c SHA256SUMS.txt" in text


def test_notes_without_sums_still_disclose_unsigned(tmp_path: Path) -> None:
    gate = _module()
    _write_repo(tmp_path)
    text = gate.render_release_notes(
        version=_VERSION,
        tag=f"v{_VERSION}",
        sha="cafebabe",
        changelog_body="body",
        sums_entries=None,
    )
    assert "未签名" in text
    assert "SHA256SUMS.txt" in text


def test_cli_notes_writes_file(tmp_path: Path) -> None:
    gate = _module()
    _write_repo(tmp_path)
    sums = tmp_path / "SHA256SUMS.txt"
    sums.write_text(f"{'c' * 64}  ScribeForge-Setup-x64.exe\n", encoding="utf-8")
    out = tmp_path / "notes.md"
    code = gate.main(
        [
            "--root",
            str(tmp_path),
            "notes",
            "--version",
            _VERSION,
            "--tag",
            f"v{_VERSION}",
            "--sha",
            "abc123",
            "--sums",
            str(sums),
            "--out",
            str(out),
        ]
    )
    assert code == 0
    content = out.read_text(encoding="utf-8")
    assert "abc123" in content
    assert "Test entry." in content


def test_cli_notes_fail_on_missing_section(tmp_path: Path) -> None:
    gate = _module()
    _write_repo(tmp_path, skip={"changelog"})
    (tmp_path / "CHANGELOG.md").write_text("## [Unreleased]\n", encoding="utf-8")
    out = tmp_path / "notes.md"
    code = gate.main(
        [
            "--root",
            str(tmp_path),
            "notes",
            "--version",
            _VERSION,
            "--tag",
            f"v{_VERSION}",
            "--sha",
            "abc",
            "--out",
            str(out),
        ]
    )
    assert code == 1
    assert not out.exists()
