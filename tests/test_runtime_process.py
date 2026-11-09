from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from threading import Timer

import pytest

from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    cancellation_scope,
)
from scribeforge.runtime.manager import (
    RuntimeCommandOutputLimitError,
    RuntimeCommandTimeoutError,
    SubprocessExecutor,
)


def _sleeper_with_child(tmp_path: Path) -> tuple[Path, Path, Path]:
    worker = tmp_path / "install_with_child.py"
    child_started = tmp_path / "child-started"
    child_survived = tmp_path / "child-survived"
    child_code = (
        "import pathlib,time; "
        f"pathlib.Path({str(child_started)!r}).write_text('started'); "
        "time.sleep(1.5); "
        f"pathlib.Path({str(child_survived)!r}).write_text('survived')"
    )
    worker.write_text(
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )
    return worker, child_started, child_survived


def test_subprocess_executor_merges_environment_and_preserves_failure_detail(
    tmp_path: Path,
) -> None:
    script = tmp_path / "fail.py"
    script.write_text(
        "import os,sys\n"
        "sys.stderr.write(os.environ.get('SCRIBEFORGE_TEST', 'missing'))\n"
        "sys.exit(7)\n",
        encoding="utf-8",
    )

    with pytest.raises(subprocess.CalledProcessError) as caught:
        SubprocessExecutor().run(
            (sys.executable, str(script)),
            {"SCRIBEFORGE_TEST": "bounded diagnostic"},
            timeout_seconds=2,
        )

    assert caught.value.returncode == 7
    assert caught.value.stderr == "bounded diagnostic"


def test_subprocess_executor_drops_external_uv_source_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "environment.json"
    script = tmp_path / "environment.py"
    script.write_text(
        "import json,os,pathlib\n"
        f"pathlib.Path({str(output)!r}).write_text(json.dumps({{"
        "key: os.environ.get(key) for key in "
        "('UV_INDEX', 'UV_EXTRA_INDEX_URL', 'UV_INDEX_STRATEGY', 'UV_CACHE_DIR')"
        "}))",
        encoding="utf-8",
    )
    monkeypatch.setenv("UV_INDEX", "https://untrusted.invalid/simple")
    monkeypatch.setenv("UV_EXTRA_INDEX_URL", "https://untrusted.invalid/extra")
    monkeypatch.setenv("UV_INDEX_STRATEGY", "unsafe-best-match")

    SubprocessExecutor().run(
        (sys.executable, str(script)),
        {"UV_CACHE_DIR": str(tmp_path / "managed-cache")},
        timeout_seconds=2,
    )

    environment = json.loads(output.read_text(encoding="utf-8"))
    assert environment == {
        "UV_INDEX": None,
        "UV_EXTRA_INDEX_URL": None,
        "UV_INDEX_STRATEGY": None,
        "UV_CACHE_DIR": str(tmp_path / "managed-cache"),
    }


def test_install_command_timeout_kills_its_process_tree(tmp_path: Path) -> None:
    worker, child_started, child_survived = _sleeper_with_child(tmp_path)
    started = time.monotonic()

    with pytest.raises(RuntimeCommandTimeoutError, match=r"0\.7 seconds"):
        SubprocessExecutor().run((sys.executable, str(worker)), timeout_seconds=0.7)

    assert time.monotonic() - started < 4
    assert child_started.exists()
    time.sleep(1.0)
    assert not child_survived.exists(), "setup timeout left an installer child running"


def test_install_cancellation_kills_process_tree(tmp_path: Path) -> None:
    worker, child_started, child_survived = _sleeper_with_child(tmp_path)
    token = CancellationToken()
    timer = Timer(0.7, token.cancel)
    timer.start()

    with cancellation_scope(token), pytest.raises(ProcessingCancelled, match="cancel"):
        SubprocessExecutor().run((sys.executable, str(worker)), timeout_seconds=5)

    timer.join(timeout=1)
    assert child_started.exists()
    time.sleep(1.0)
    assert not child_survived.exists(), "setup cancellation left a child running"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_install_output_is_disk_bounded(tmp_path: Path, stream: str) -> None:
    worker = tmp_path / "noisy_install.py"
    worker.write_text(
        "import sys,time\n"
        f"sys.{stream}.write('x' * 1000000)\n"
        f"sys.{stream}.flush()\n"
        "time.sleep(10)\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeCommandOutputLimitError, match=stream):
        SubprocessExecutor(max_output_bytes=1024, max_log_bytes=1024).run(
            (sys.executable, str(worker)), timeout_seconds=5
        )


def test_subprocess_executor_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="timeout"):
        SubprocessExecutor().run(("tool",), timeout_seconds=0)
    with pytest.raises(ValueError, match="limits"):
        SubprocessExecutor(max_output_bytes=0)
