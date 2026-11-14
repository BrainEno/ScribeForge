"""Lightweight structured logging for issue #69.

Standard-library ``logging`` only: a named ``scribeforge`` logger with a level,
an optional rotating file handler, and a formatter that redacts the user's home
directory from every emitted line so log files stay safe to share. The logger
is configured once per CLI invocation and never propagates to the root logger,
so library use stays silent unless the application opts in.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

LOGGER_NAME = "scribeforge"

#: Declared CLI level names mapped to their numeric values.
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}
DEFAULT_LOG_LEVEL = "info"

#: Placeholder substituted for the user's home directory in log output.
REDACTED_HOME = "~"

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

#: Declared CLI log format names.
LOG_FORMATS = ("text", "json")
DEFAULT_LOG_FORMAT = "text"

#: Placeholder substituted for secret-looking values in log output.
REDACTED_SECRET = "<redacted>"

#: Secret shapes masked by :func:`redact_text` in addition to the home dir.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Authorization headers: "Bearer <token>" (scheme kept, token masked).
    re.compile(r"(Bearer\s+)([A-Za-z0-9._~+/=-]+)", re.IGNORECASE),
    # Well-known API key prefixes: sk-..., pk-..., ghp_..., xoxb-... etc.
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghu|ghs|ghr|xox[baprs])[-_][A-Za-z0-9_-]{8,}\b"),
    # key=value / key: value credentials; the key name is kept, the value masked.
    re.compile(
        r"\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?secret"
        r"|password|passwd|secret)\b(\s*[:=]\s*)(\S+)",
        re.IGNORECASE,
    ),
)

#: Run id of the current invocation; set by :func:`configure_logging`.
_RUN_ID: ContextVar[str] = ContextVar("scribeforge_run_id", default="")


def parse_log_format(value: str) -> str:
    """Return the declared log format name (``text`` or ``json``)."""

    normalized = value.strip().lower()
    if normalized not in LOG_FORMATS:
        supported = ", ".join(LOG_FORMATS)
        raise ValueError(f"unsupported log format {value!r}; supported formats: {supported}")
    return normalized


def new_run_id() -> str:
    """Return a fresh short run id (8 hex chars) for one CLI invocation."""

    return secrets.token_hex(4)


def get_run_id() -> str:
    """Return the current run id (empty string when logging was never configured)."""

    return _RUN_ID.get()


def set_run_id(value: str) -> None:
    """Set the current run id (used by :func:`configure_logging` and tests)."""

    _RUN_ID.set(value)


def parse_log_level(value: str) -> int:
    """Return the numeric level for a declared level name."""

    try:
        return LOG_LEVELS[value.strip().lower()]
    except KeyError:
        supported = ", ".join(sorted(LOG_LEVELS))
        raise ValueError(
            f"unsupported log level {value!r}; supported levels: {supported}"
        ) from None


def redact_text(text: str, *, home: Path | None = None) -> str:
    """Replace the home directory prefix with ``~`` so paths stay shareable.

    The redacted prefix is always followed by ``/``: on Windows the native
    separator is ``\\``, and a shared log line should read ``~/secret``, not
    ``~\\secret`` (which JSON encoding would double-escape).
    """

    home_path = home if home is not None else Path.home()
    home_str = str(home_path)
    if home_str and home_str != "/":
        # Match both separator styles so a Windows-style path is redacted even
        # on a POSIX host (and vice versa).
        text = text.replace(home_str, REDACTED_HOME)
        text = text.replace(home_str.replace("/", "\\"), REDACTED_HOME)
        text = text.replace(REDACTED_HOME + "\\", REDACTED_HOME + "/")
    # Mask common secret shapes (tokens, API keys, key=value credentials):
    # keep every capture group except the last (the secret itself).
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(_mask_last_group, text)
        else:
            text = pattern.sub(REDACTED_SECRET, text)
    return text


def _mask_last_group(match: re.Match[str]) -> str:
    """Keep all capture groups except the last one, which is masked."""

    groups = match.re.groups
    return "".join(match.group(i) for i in range(1, groups)) + REDACTED_SECRET


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the home directory from the final rendered line."""

    def __init__(self, fmt: str = _LOG_FORMAT, *, home: Path | None = None) -> None:
        super().__init__(fmt)
        self._home = home

    def format(self, record: logging.LogRecord) -> str:
        # Redact after formatting so exception tracebacks are covered too.
        return redact_text(super().format(record), home=self._home)


class JsonFormatter(logging.Formatter):
    """Formatter emitting one JSON object per line (``--log-format json``).

    Schema per line: ``ts`` (ISO-ish local time), ``level``, ``logger``,
    ``run`` (the current run id), ``message``, and ``exception`` when the
    record carries traceback info. Message and exception text are redacted
    (home directory and secret shapes) *before* JSON encoding.
    """

    def __init__(self, *, home: Path | None = None) -> None:
        super().__init__(datefmt="%Y-%m-%dT%H:%M:%S%z")
        self._home = home

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "run": get_run_id(),
            "message": redact_text(record.getMessage(), home=self._home),
        }
        if record.exc_info:
            payload["exception"] = redact_text(
                self.formatException(record.exc_info), home=self._home
            )
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "") -> logging.Logger:
    """Return a child logger under the shared ``scribeforge`` namespace."""

    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def configure_logging(
    level: str | int = DEFAULT_LOG_LEVEL,
    log_file: str | Path | None = None,
    *,
    log_format: str = DEFAULT_LOG_FORMAT,
    home: Path | None = None,
    stream: IO[str] | None = None,
) -> logging.Logger:
    """Configure the ``scribeforge`` logger; safe to call repeatedly.

    Existing handlers are removed first, so repeated calls (one per CLI
    invocation, or in tests) never duplicate output. A console handler on
    ``stream`` (default ``sys.stderr``) is always attached; when ``log_file``
    is given, a rotating file handler is attached as well. Every handler uses
    :class:`RedactingFormatter` (``text``) or :class:`JsonFormatter` (``json``).
    Each call also assigns a fresh run id, visible via :func:`get_run_id` and
    embedded in JSON log lines and pipeline event lines.
    """

    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    numeric = parse_log_level(level) if isinstance(level, str) else level
    logger.setLevel(numeric)
    logger.propagate = False
    set_run_id(new_run_id())

    if parse_log_format(log_format) == "json":
        formatter: logging.Formatter = JsonFormatter(home=home)
    else:
        formatter = RedactingFormatter(home=home)
    console = logging.StreamHandler(stream)
    # The console stays quiet unless something is wrong so CLI stdout/stderr
    # contracts (and scripts piping them) are undisturbed; the configured
    # level applies to the logger and to the optional file handler.
    console.setLevel(logging.WARNING)
    console.setFormatter(formatter)
    logger.addHandler(console)

    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler: RotatingFileHandler = RotatingFileHandler(
            path,
            maxBytes=DEFAULT_MAX_BYTES,
            backupCount=DEFAULT_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger
