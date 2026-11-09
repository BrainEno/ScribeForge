"""Lightweight structured logging for issue #69.

Standard-library ``logging`` only: a named ``scribeforge`` logger with a level,
an optional rotating file handler, and a formatter that redacts the user's home
directory from every emitted line so log files stay safe to share. The logger
is configured once per CLI invocation and never propagates to the root logger,
so library use stays silent unless the application opts in.
"""

from __future__ import annotations

import logging
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
    """Replace the home directory prefix with ``~`` so paths stay shareable."""

    home_path = home if home is not None else Path.home()
    home_str = str(home_path)
    if not home_str or home_str == "/":
        return text
    return text.replace(home_str, REDACTED_HOME)


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts the home directory from the final rendered line."""

    def __init__(self, fmt: str = _LOG_FORMAT, *, home: Path | None = None) -> None:
        super().__init__(fmt)
        self._home = home

    def format(self, record: logging.LogRecord) -> str:
        # Redact after formatting so exception tracebacks are covered too.
        return redact_text(super().format(record), home=self._home)


def get_logger(name: str = "") -> logging.Logger:
    """Return a child logger under the shared ``scribeforge`` namespace."""

    return logging.getLogger(LOGGER_NAME if not name else f"{LOGGER_NAME}.{name}")


def configure_logging(
    level: str | int = DEFAULT_LOG_LEVEL,
    log_file: str | Path | None = None,
    *,
    home: Path | None = None,
    stream: IO[str] | None = None,
) -> logging.Logger:
    """Configure the ``scribeforge`` logger; safe to call repeatedly.

    Existing handlers are removed first, so repeated calls (one per CLI
    invocation, or in tests) never duplicate output. A console handler on
    ``stream`` (default ``sys.stderr``) is always attached; when ``log_file``
    is given, a rotating file handler is attached as well. Every handler uses
    :class:`RedactingFormatter`.
    """

    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    numeric = parse_log_level(level) if isinstance(level, str) else level
    logger.setLevel(numeric)
    logger.propagate = False

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
