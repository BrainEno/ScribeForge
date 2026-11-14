"""Named optional backends for the technology route (issue #90).

`build_runtime_plan` already plans `paddleocr-vl` and `surya` as optional
backends; this slice makes them first-class by *name* everywhere a user
touches them: `setup --backend <name>` repairs them, and `runtime status`
reports their health next to mineru/paddleocr. Planner-level tests pin the
plan shape (health checks, optional flag, on-disk lock files).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribeforge.runtime.models import HardwareProfile, RuntimeLayout
from scribeforge.runtime.planner import build_runtime_plan

_OPTIONAL = ("paddleocr-vl", "surya")


def _plan(tmp_path: Path):
    profile = HardwareProfile("linux", "x86_64", None, None, None, 16 * 1024)
    layout = RuntimeLayout(tmp_path / "runtime")
    return build_runtime_plan(profile, layout, uv_executable="/fake/uv")


def test_plan_contains_all_four_backends_by_name(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    names = [backend.name for backend in plan.backends]

    assert names == ["mineru", "paddleocr", "paddleocr-vl", "surya"]
    assert plan.backend("mineru").optional is False
    assert plan.backend("paddleocr").optional is False
    for name in _OPTIONAL:
        assert plan.backend(name).optional is True


def test_every_backend_has_a_health_check_and_existing_lock_file(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    for backend in plan.backends:
        assert backend.health_checks, f"{backend.name} has no health check"
        for step in backend.steps:
            for token in step.argv:
                if token.endswith(".txt") and "locks" in token:
                    lock_path = Path(token)
                    assert lock_path.is_file(), f"missing lock file {lock_path}"


def test_plan_backend_rejects_unknown_names(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    with pytest.raises(KeyError):
        plan.backend("surya-ocr")  # near-miss must not match
    with pytest.raises(KeyError):
        plan.backend("marker")


def test_setup_backend_accepts_optional_route_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys
    import types

    from scribeforge.runtime.manager import RuntimeEvent
    from scribeforge.runtime.models import HardwareProfile

    runtime_root = tmp_path / "private-runtime"
    profile = HardwareProfile("linux", "x86_64", None, None, None, 16 * 1024)
    calls: list[str] = []

    class FakeSetupWorkflow:
        def __init__(self, root: Path) -> None:
            assert root == runtime_root

        def repair(self, backend: str, *, on_event) -> None:
            calls.append(backend)
            on_event(RuntimeEvent(backend, "models", "step_completed", "download", 1, 1))

        def inspect(self):
            from scribeforge.runtime.manager import RuntimeStatus

            return types.SimpleNamespace(
                hardware=profile,
                uv_available=True,
                mineru=RuntimeStatus("ready"),
                paddleocr=RuntimeStatus("ready"),
                optional_backends={name: RuntimeStatus("ready") for name in _OPTIONAL},
            )

    module = types.ModuleType("scribeforge.runtime.workflow")
    module.RuntimeSetupWorkflow = FakeSetupWorkflow
    monkeypatch.setitem(sys.modules, "scribeforge.runtime.workflow", module)

    for backend in _OPTIONAL:
        output = io.StringIO()
        from scribeforge.cli import run

        code = run(
            [
                "setup",
                "--runtime-root",
                str(runtime_root),
                "--backend",
                backend,
                "--json",
            ],
            stdout=output,
        )
        assert code == 0, output.getvalue()
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        assert records[-1]["action"] == "repair"
        assert records[-1]["success"] is True

    assert calls == list(_OPTIONAL)


def test_setup_backend_rejects_unknown_name(tmp_path: Path) -> None:
    from scribeforge.cli import run

    with pytest.raises(SystemExit) as excinfo:
        run(["setup", "--backend", "marker", "--json"], stdout=__import__("io").StringIO())
    assert excinfo.value.code == 2  # argparse rejects the choice


def test_runtime_status_reports_optional_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys
    import types

    from scribeforge.runtime.manager import RuntimeStatus
    from scribeforge.runtime.models import HardwareProfile

    runtime_root = tmp_path / "private-runtime"
    profile = HardwareProfile("linux", "x86_64", None, None, None, 16 * 1024)

    class FakeSetupWorkflow:
        def __init__(self, root: Path) -> None:
            assert root == runtime_root

        def inspect(self):
            return types.SimpleNamespace(
                hardware=profile,
                uv_available=True,
                mineru=RuntimeStatus("ready"),
                paddleocr=RuntimeStatus("ready"),
                optional_backends={
                    "paddleocr-vl": RuntimeStatus("missing"),
                    "surya": RuntimeStatus("failed", "models:download"),
                },
            )

    module = types.ModuleType("scribeforge.runtime.workflow")
    module.RuntimeSetupWorkflow = FakeSetupWorkflow
    monkeypatch.setitem(sys.modules, "scribeforge.runtime.workflow", module)

    from scribeforge.cli import run

    output = io.StringIO()
    code = run(["runtime-status", "--runtime-root", str(runtime_root), "--json"], stdout=output)

    assert code == 0
    payload = json.loads(output.getvalue())
    backends = payload["backends"]
    assert set(backends) == {"mineru", "paddleocr", "paddleocr-vl", "surya"}
    assert backends["mineru"]["optional"] is False
    assert backends["surya"]["optional"] is True
    assert backends["surya"]["state"] == "failed"
    assert backends["surya"]["failed_step"] == "models:download"
    assert backends["paddleocr-vl"]["state"] == "missing"


def test_runtime_status_legacy_overview_without_optional_backends_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older/fake overviews lacking the new field must not break status."""
    import io
    import sys
    import types

    from scribeforge.runtime.manager import RuntimeStatus
    from scribeforge.runtime.models import HardwareProfile

    runtime_root = tmp_path / "private-runtime"
    profile = HardwareProfile("linux", "x86_64", None, None, None, 16 * 1024)

    class FakeSetupWorkflow:
        def __init__(self, root: Path) -> None:
            pass

        def inspect(self):
            return types.SimpleNamespace(
                hardware=profile,
                uv_available=True,
                mineru=RuntimeStatus("ready"),
                paddleocr=RuntimeStatus("ready"),
            )

    module = types.ModuleType("scribeforge.runtime.workflow")
    module.RuntimeSetupWorkflow = FakeSetupWorkflow
    monkeypatch.setitem(sys.modules, "scribeforge.runtime.workflow", module)

    from scribeforge.cli import run

    output = io.StringIO()
    code = run(["runtime-status", "--runtime-root", str(runtime_root), "--json"], stdout=output)

    assert code == 0
    payload = json.loads(output.getvalue())
    assert set(payload["backends"]) >= {"mineru", "paddleocr"}


def test_optional_backend_health_checks_probe_the_right_module(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    vl = plan.backend("paddleocr-vl")
    vl_health = " ".join(" ".join(step.argv) for step in vl.health_checks)
    assert "PaddleOCRVL" in vl_health

    surya = plan.backend("surya")
    surya_health = " ".join(" ".join(step.argv) for step in surya.health_checks)
    assert "surya" in surya_health


def test_runtime_status_markdown_lists_optional_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys
    import types

    from scribeforge.runtime.manager import RuntimeStatus
    from scribeforge.runtime.models import HardwareProfile

    runtime_root = tmp_path / "private-runtime"
    profile = HardwareProfile("linux", "x86_64", None, None, None, 16 * 1024)

    class FakeSetupWorkflow:
        def __init__(self, root: Path) -> None:
            pass

        def inspect(self):
            return types.SimpleNamespace(
                hardware=profile,
                uv_available=True,
                mineru=RuntimeStatus("ready"),
                paddleocr=RuntimeStatus("ready"),
                optional_backends={"surya": RuntimeStatus("missing")},
            )

    module = types.ModuleType("scribeforge.runtime.workflow")
    module.RuntimeSetupWorkflow = FakeSetupWorkflow
    monkeypatch.setitem(sys.modules, "scribeforge.runtime.workflow", module)

    from scribeforge.cli import run

    output = io.StringIO()
    code = run(["runtime-status", "--runtime-root", str(runtime_root)], stdout=output)

    assert code == 0
    text = output.getvalue()
    assert "mineru: ready" in text
    assert "surya: missing" in text


def test_workflow_inspect_reports_optional_backends(tmp_path: Path) -> None:
    """The real workflow fills optional_backends from the manager state file."""

    from scribeforge.runtime.manager import RuntimeManager
    from scribeforge.runtime.models import HardwareProfile
    from scribeforge.runtime.workflow import RuntimeSetupWorkflow

    root = tmp_path / "runtime"
    root.mkdir(parents=True)
    manager = RuntimeManager(root)
    manager._set_status("surya", manager.status("surya").__class__("ready"))

    profile = HardwareProfile("linux", "x86_64", None, None, None, 16 * 1024)
    workflow = RuntimeSetupWorkflow(
        root,
        hardware_probe=lambda: profile,
        uv_resolver=lambda tools_dir, os_name: (_ for _ in ()).throw(
            __import__(
                "scribeforge.runtime.tools", fromlist=["RuntimeToolMissing"]
            ).RuntimeToolMissing("uv")
        ),
    )

    overview = workflow.inspect()

    assert set(overview.optional_backends) == {"paddleocr-vl", "surya"}
    assert overview.optional_backends["surya"].state == "ready"
    assert overview.optional_backends["paddleocr-vl"].state == "missing"


def test_setup_backend_still_accepts_core_backends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import sys
    import types

    from scribeforge.runtime.manager import RuntimeStatus
    from scribeforge.runtime.models import HardwareProfile

    runtime_root = tmp_path / "private-runtime"
    profile = HardwareProfile("linux", "x86_64", None, None, None, 16 * 1024)
    calls: list[str] = []

    class FakeSetupWorkflow:
        def __init__(self, root: Path) -> None:
            pass

        def repair(self, backend: str, *, on_event) -> None:
            calls.append(backend)

        def inspect(self):
            return types.SimpleNamespace(
                hardware=profile,
                uv_available=True,
                mineru=RuntimeStatus("ready"),
                paddleocr=RuntimeStatus("ready"),
                optional_backends={},
            )

    module = types.ModuleType("scribeforge.runtime.workflow")
    module.RuntimeSetupWorkflow = FakeSetupWorkflow
    monkeypatch.setitem(sys.modules, "scribeforge.runtime.workflow", module)

    from scribeforge.cli import run

    for backend in ("mineru", "paddleocr"):
        code = run(
            ["setup", "--runtime-root", str(runtime_root), "--backend", backend, "--json"],
            stdout=io.StringIO(),
        )
        assert code == 0
    assert calls == ["mineru", "paddleocr"]


def test_optional_backends_stay_out_of_default_install_plan(tmp_path: Path) -> None:
    """Optional route backends must not run during a default `setup` install."""
    plan = _plan(tmp_path)

    core = [b.name for b in plan.backends if not b.optional]
    optional = [b.name for b in plan.backends if b.optional]
    assert core == ["mineru", "paddleocr"]
    assert optional == ["paddleocr-vl", "surya"]
