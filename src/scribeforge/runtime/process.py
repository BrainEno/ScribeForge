from __future__ import annotations

import math
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from typing import Any, BinaryIO

from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    current_cancellation_token,
)

_PROCESS_POLL_INTERVAL_SECONDS = 0.05
_MAX_ERROR_DETAIL_BYTES = 32 * 1024


class RuntimeCommandTimeoutError(TimeoutError):
    """A runtime installation command exceeded its overall deadline."""


class RuntimeCommandOutputLimitError(RuntimeError):
    """A runtime command exceeded its bounded stdout or stderr allowance."""


def run_runtime_process(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None,
    timeout_seconds: float,
    max_output_bytes: int,
    max_log_bytes: int,
) -> tuple[int, str, str]:
    """Run one setup command with disk-backed output and tree cleanup."""

    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("runtime command timeout must be finite and greater than zero")
    if max_output_bytes < 1 or max_log_bytes < 1:
        raise ValueError("runtime command output limits must be positive")
    token = current_cancellation_token()
    if token is not None:
        token.raise_if_cancelled()

    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process_options: dict[str, Any] = {}
        if os.name == "nt":
            process_options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            process_options["start_new_session"] = True
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=None if environment is None else dict(environment),
            **process_options,
        )
        started_at = time.monotonic()
        try:
            while process.poll() is None:
                _check_control(
                    token,
                    stdout_file,
                    stderr_file,
                    max_output_bytes=max_output_bytes,
                    max_log_bytes=max_log_bytes,
                )
                remaining = timeout_seconds - (time.monotonic() - started_at)
                if remaining <= 0:
                    raise RuntimeCommandTimeoutError(
                        f"runtime command timed out after {timeout_seconds:g} seconds"
                    )
                time.sleep(min(_PROCESS_POLL_INTERVAL_SECONDS, remaining))

            _check_control(
                token,
                stdout_file,
                stderr_file,
                max_output_bytes=max_output_bytes,
                max_log_bytes=max_log_bytes,
            )
            stdout_file.seek(0)
            stdout = stdout_file.read(max_output_bytes + 1).decode("utf-8", errors="replace")
            stderr = _read_tail(stderr_file, min(max_log_bytes, _MAX_ERROR_DETAIL_BYTES))
            return_code = process.returncode
            if return_code is None:
                return_code = process.wait()
            return return_code, stdout, stderr
        except RuntimeCommandTimeoutError as exc:
            _terminate_process_tree(process)
            detail = _read_tail(stderr_file, _MAX_ERROR_DETAIL_BYTES)
            if detail:
                raise RuntimeCommandTimeoutError(f"{exc}; command stderr: {detail}") from None
            raise
        except KeyboardInterrupt:
            _terminate_process_tree(process)
            raise ProcessingCancelled("processing cancelled by user") from None
        except BaseException:
            _terminate_process_tree(process)
            raise


def _check_control(
    token: CancellationToken | None,
    stdout_file: BinaryIO,
    stderr_file: BinaryIO,
    *,
    max_output_bytes: int,
    max_log_bytes: int,
) -> None:
    if token is not None:
        token.raise_if_cancelled()
    if os.fstat(stdout_file.fileno()).st_size > max_output_bytes:
        raise RuntimeCommandOutputLimitError(
            f"runtime command exceeded stdout output limit of {max_output_bytes} bytes"
        )
    if os.fstat(stderr_file.fileno()).st_size > max_log_bytes:
        raise RuntimeCommandOutputLimitError(
            f"runtime command exceeded stderr output limit of {max_log_bytes} bytes"
        )


def _read_tail(stream: BinaryIO, max_bytes: int) -> str:
    size = os.fstat(stream.fileno()).st_size
    stream.seek(max(0, size - max_bytes))
    return stream.read(max_bytes).decode("utf-8", errors="replace").strip()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Stop a setup command and every descendant it launched."""

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.terminate()
    try:
        process.wait(timeout=0.35)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
