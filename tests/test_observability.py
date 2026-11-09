"""Runtime regression for issue #69: a lightweight, redacting, rotatable logger."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scribeforge.cli import build_parser, run
from scribeforge.observability import (
    DEFAULT_LOG_LEVEL,
    LOG_LEVELS,
    configure_logging,
    get_logger,
    parse_log_level,
    redact_text,
)


@pytest.fixture(autouse=True)
def _reset_scribeforge_logger(tmp_path: Path):
    # Requesting tmp_path forces this fixture to be finalized *before*
    # tmp_path cleanup, so rotating file handlers are closed before Windows
    # tries to delete the files they hold open.
    yield
    logger = logging.getLogger("scribeforge")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = True


def test_parse_log_level_accepts_declared_names_and_rejects_others() -> None:
    assert parse_log_level("debug") == logging.DEBUG
    assert parse_log_level("INFO") == logging.INFO
    assert set(LOG_LEVELS) == {"debug", "info", "warning", "error"}
    with pytest.raises(ValueError, match="unsupported log level"):
        parse_log_level("verbose")


def test_redact_text_masks_the_home_directory(tmp_path: Path) -> None:
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    secret = f"opening {home}/book.pdf failed"
    assert redact_text(secret, home=home) == "opening ~/book.pdf failed"
    # A path that is not under home is left untouched.
    assert redact_text("/var/log/x.log", home=home) == "/var/log/x.log"


def test_configure_logging_filters_below_the_declared_level() -> None:
    logger = configure_logging("warning")
    assert logger.level == logging.WARNING
    assert logger.propagate is False
    assert len(logger.handlers) == 1  # console only, no file requested


def test_console_handler_stays_at_warning_so_cli_stderr_is_undisturbed() -> None:
    # The configured level governs the logger and any file handler; the
    # console only surfaces warnings and errors so CLI stderr contracts
    # (asserted by the CLI tests) keep holding.
    logger = configure_logging("debug")
    assert logger.level == logging.DEBUG
    console = logger.handlers[0]
    assert console.level == logging.WARNING


def test_configure_logging_is_idempotent() -> None:
    first = configure_logging("info")
    second = configure_logging("debug")
    assert first is second
    assert len(second.handlers) == 1
    assert second.level == logging.DEBUG


def test_configure_logging_adds_a_rotating_file_handler_and_redacts(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    log_file = tmp_path / "logs" / "scribeforge.log"
    logger = configure_logging("debug", log_file, home=home)

    file_handlers = [
        h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert file_handlers[0].maxBytes > 0 and file_handlers[0].backupCount > 0

    get_logger("test").info("reading %s", home / "secret.pdf")
    for handler in logger.handlers:
        handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert str(home) not in content
    assert "~/secret.pdf" in content
    for handler in logger.handlers:
        handler.close()
        logger.removeHandler(handler)


def test_cli_log_flags_configure_logging_and_write_a_redacted_file(tmp_path: Path) -> None:
    log_file = tmp_path / "run.log"
    store = tmp_path / "no-such-store"
    code = run(
        ["--log-level", "debug", "--log-file", str(log_file), "status", "--store", str(store)]
    )
    assert code != 0  # store does not exist; the point is the logging side effect
    for handler in logging.getLogger("scribeforge").handlers:
        handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert "status" in content
    assert str(Path.home()) not in content
    for handler in logging.getLogger("scribeforge").handlers:
        handler.close()
        logging.getLogger("scribeforge").removeHandler(handler)


def test_cli_defaults_to_info_without_a_log_file(tmp_path: Path) -> None:
    parser_levels = {action.dest for action in build_parser()._actions}
    assert {"log_level", "log_file"} <= parser_levels
    logger = configure_logging(DEFAULT_LOG_LEVEL)
    assert logger.level == logging.INFO
    assert not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers)
