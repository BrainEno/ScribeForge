from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, BinaryIO

from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    current_cancellation_token,
)
from scribeforge.domain.ocr import (
    BlockType,
    BoundingBox,
    OCRBlock,
    OCRLine,
    OCRToken,
    PageDirection,
    PageOCRResult,
)
from scribeforge.workers.protocol import SUPPORTED_SCHEMA_VERSIONS, canonical_block_label

DEFAULT_WORKER_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_WORKER_OUTPUT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_WORKER_LOG_BYTES = 4 * 1024 * 1024
_MAX_ERROR_DETAIL_BYTES = 32 * 1024
_PROCESS_POLL_INTERVAL_SECONDS = 0.05


class OCREngineTimeoutError(TimeoutError):
    """A worker did not finish before its configured per-page deadline."""


class OCREngineOutputLimitError(RuntimeError):
    """A worker exceeded the bounded stdout or stderr allowance."""


def block_type_from_label(label: object) -> BlockType:
    """Map an engine label to a :class:`BlockType` without ever failing a page.

    An unrecognised label becomes ``UNKNOWN`` rather than raising: a new engine
    category must not make previously readable pages unprocessable. The label
    aliases live in the worker contract so workers and the adapter agree.
    """

    return BlockType(canonical_block_label(label))


def direction_from_value(value: object) -> PageDirection:
    """Map a worker direction string, defaulting to horizontal left-to-right."""

    if isinstance(value, str):
        for direction in PageDirection:
            if direction.value == value.strip().lower():
                return direction
    return PageDirection.HORIZONTAL_LR


class SubprocessOCREngine:
    """Adapter for isolated OCR workers using a versioned JSON stdout contract.

    An optional environment is merged over the process environment, and an
    optional preflight hook runs before each page so a backend that is no longer
    healthy fails loudly instead of silently producing wrong evidence.
    """

    def __init__(
        self,
        name: str,
        command: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        preflight: Callable[[], None] | None = None,
        timeout_seconds: float = DEFAULT_WORKER_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_WORKER_OUTPUT_BYTES,
        max_log_bytes: int = DEFAULT_MAX_WORKER_LOG_BYTES,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("worker timeout must be finite and greater than zero")
        if max_output_bytes < 1 or max_log_bytes < 1:
            raise ValueError("worker output and log limits must be positive")
        self._name = name
        self._command = tuple(command)
        self._environment = None if environment is None else dict(environment)
        self._preflight = preflight
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._max_log_bytes = max_log_bytes

    @property
    def name(self) -> str:
        return self._name

    @property
    def timeout_seconds(self) -> float:
        """Configured per-page worker deadline."""

        return self._timeout_seconds

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        if self._preflight is not None:
            self._preflight()
        process_env = None
        if self._environment:
            process_env = os.environ.copy()
            process_env.update(self._environment)
        command = [*self._command, "--image", image_path, "--page-index", str(page_index)]
        return_code, stdout, stderr = _run_worker_process(
            command,
            environment=process_env,
            engine_name=self._name,
            page_index=page_index,
            timeout_seconds=self._timeout_seconds,
            max_output_bytes=self._max_output_bytes,
            max_log_bytes=self._max_log_bytes,
            cancellation_token=current_cancellation_token(),
        )
        if return_code != 0:
            raise subprocess.CalledProcessError(
                return_code,
                command,
                output=stdout,
                stderr=stderr,
            )
        return self.parse_document(json.loads(stdout), page_index)

    def parse_document(self, raw: Any, page_index: int) -> PageOCRResult:
        """Parse a worker result document of any supported schema version."""

        if not isinstance(raw, dict):
            raise TypeError("OCR worker output must be a JSON object")
        schema_version = raw.get("schema_version")
        if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError("unsupported OCR worker schema")
        if raw.get("engine") != self._name:
            raise ValueError("OCR worker engine identity mismatch")
        if raw.get("page_index") != page_index:
            raise ValueError("OCR worker page identity mismatch")

        lines = tuple(self._line(item) for item in raw.get("lines", []))
        result = PageOCRResult(
            page_index=page_index,
            engine=self._name,
            engine_version=str(raw["engine_version"]),
            lines=lines,
            blocks=self._blocks(raw.get("blocks", []), line_count=len(lines)),
            direction=direction_from_value(raw.get("direction")),
            direction_declared=raw.get("direction") is not None,
        )
        return result

    @staticmethod
    def _box(values: Any) -> BoundingBox:
        if not isinstance(values, list) or len(values) != 4:
            raise ValueError("OCR worker box must contain x, y, width, height")
        return BoundingBox(*(float(value) for value in values))

    @staticmethod
    def _blocks(raw: Any, *, line_count: int) -> tuple[OCRBlock, ...]:
        if raw is None:
            return ()
        if not isinstance(raw, list):
            raise TypeError("OCR worker blocks must be a list")

        blocks: list[OCRBlock] = []
        for item in raw:
            if not isinstance(item, dict):
                raise TypeError("OCR worker block must be an object")
            raw_start = item.get("line_start", 0)
            raw_end = item.get("line_end", line_count)
            if not isinstance(raw_start, int) or not isinstance(raw_end, int):
                raise TypeError("OCR worker block line range must be integers")
            blocks.append(
                OCRBlock(
                    block_type=block_type_from_label(item.get("type")),
                    box=SubprocessOCREngine._box(item.get("box")),
                    line_start=raw_start,
                    line_end=raw_end,
                )
            )
        return tuple(blocks)

    @classmethod
    def _line(cls, raw: Any) -> OCRLine:
        if not isinstance(raw, dict):
            raise TypeError("OCR worker line must be an object")
        raw_tokens = raw.get("tokens")
        if raw_tokens is None:
            tokens = None
        else:
            if not isinstance(raw_tokens, list):
                raise TypeError("OCR worker line tokens must be a list or null")
            tokens = tuple(
                OCRToken(
                    text=str(token["text"]),
                    confidence=(
                        None
                        if token.get("confidence") is None
                        else float(token["confidence"])
                    ),
                    box=cls._box(token["box"]),
                )
                for token in raw_tokens
            )
        raw_confidence = raw.get("confidence")
        return OCRLine(
            text=str(raw["text"]),
            box=cls._box(raw["box"]),
            tokens=tokens,
            confidence=None if raw_confidence is None else float(raw_confidence),
        )


def _run_worker_process(
    command: Sequence[str],
    *,
    environment: Mapping[str, str] | None,
    engine_name: str,
    page_index: int,
    timeout_seconds: float,
    max_output_bytes: int,
    max_log_bytes: int,
    cancellation_token: CancellationToken | None,
) -> tuple[int, str, str]:
    """Run one worker with bounded disk-backed output and process-tree cleanup."""

    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process_options: dict[str, Any] = {}
        if os.name == "nt":
            process_options["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
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
                if cancellation_token is not None:
                    cancellation_token.raise_if_cancelled()
                stdout_size = os.fstat(stdout_file.fileno()).st_size
                stderr_size = os.fstat(stderr_file.fileno()).st_size
                if stdout_size > max_output_bytes:
                    raise OCREngineOutputLimitError(
                        f"OCR worker {engine_name} exceeded stdout output limit "
                        f"of {max_output_bytes} bytes on page {page_index + 1}"
                    )
                if stderr_size > max_log_bytes:
                    raise OCREngineOutputLimitError(
                        f"OCR worker {engine_name} exceeded stderr log limit "
                        f"of {max_log_bytes} bytes on page {page_index + 1}"
                    )
                remaining = timeout_seconds - (time.monotonic() - started_at)
                if remaining <= 0:
                    raise OCREngineTimeoutError(
                        f"OCR worker {engine_name} timed out on page {page_index + 1} "
                        f"after {timeout_seconds:g} seconds"
                    )
                time.sleep(min(_PROCESS_POLL_INTERVAL_SECONDS, remaining))

            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            stdout_size = os.fstat(stdout_file.fileno()).st_size
            stderr_size = os.fstat(stderr_file.fileno()).st_size
            if stdout_size > max_output_bytes:
                raise OCREngineOutputLimitError(
                    f"OCR worker {engine_name} exceeded stdout output limit "
                    f"of {max_output_bytes} bytes on page {page_index + 1}"
                )
            if stderr_size > max_log_bytes:
                raise OCREngineOutputLimitError(
                    f"OCR worker {engine_name} exceeded stderr log limit "
                    f"of {max_log_bytes} bytes on page {page_index + 1}"
                )
            stdout_file.seek(0)
            stdout = stdout_file.read(max_output_bytes + 1).decode("utf-8", errors="replace")
            stderr = _read_tail(stderr_file, min(max_log_bytes, _MAX_ERROR_DETAIL_BYTES))
            return_code = process.returncode
            if return_code is None:
                return_code = process.wait()
            return return_code, stdout, stderr
        except OCREngineTimeoutError as exc:
            _terminate_process_tree(process)
            detail = _read_tail(stderr_file, _MAX_ERROR_DETAIL_BYTES)
            if detail:
                raise OCREngineTimeoutError(f"{exc}; worker stderr: {detail}") from None
            raise
        except KeyboardInterrupt:
            _terminate_process_tree(process)
            raise ProcessingCancelled("processing cancelled by user") from None
        except BaseException:
            _terminate_process_tree(process)
            raise


def _read_tail(stream: BinaryIO, max_bytes: int) -> str:
    size = os.fstat(stream.fileno()).st_size
    stream.seek(max(0, size - max_bytes))
    return stream.read(max_bytes).decode("utf-8", errors="replace").strip()


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Terminate a worker and descendants, including engine CLIs it launched."""

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
