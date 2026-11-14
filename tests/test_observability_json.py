"""Structured JSON logging, run ids and secret redaction (issue #69).

`--log-format json` makes every log line a single JSON object with a stable
schema (ts / level / logger / run / message), so log files can be shipped to
log aggregators. Each CLI invocation gets a fresh `run` id that also tags the
JSONL pipeline event log, so log lines and events can be correlated. Redaction
covers the home directory *and* common secret shapes (bearer tokens, API keys,
key=value credentials) in both text and JSON output.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest

from scribeforge.observability import (
    DEFAULT_LOG_FORMAT,
    LOG_FORMATS,
    JsonFormatter,
    RedactingFormatter,
    configure_logging,
    get_logger,
    get_run_id,
    parse_log_format,
    redact_text,
    set_run_id,
)


@pytest.fixture(autouse=True)
def _reset_scribeforge_logger(tmp_path: Path):
    # tmp_path forces this fixture to finalize before tmp_path cleanup so
    # rotating file handlers are closed before Windows deletes their files.
    yield
    logger = logging.getLogger("scribeforge")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.propagate = True
    set_run_id("")


# ---------------------------------------------------------------------------
# log format parsing
# ---------------------------------------------------------------------------


def test_parse_log_format_accepts_text_and_json_only() -> None:
    assert parse_log_format("text") == "text"
    assert parse_log_format("JSON") == "json"
    assert set(LOG_FORMATS) == {"text", "json"}
    assert DEFAULT_LOG_FORMAT == "text"
    with pytest.raises(ValueError, match="unsupported log format"):
        parse_log_format("xml")


# ---------------------------------------------------------------------------
# run ids
# ---------------------------------------------------------------------------


def test_configure_logging_assigns_a_fresh_run_id() -> None:
    configure_logging("info")
    first = get_run_id()
    assert re.fullmatch(r"[0-9a-f]{8}", first)
    configure_logging("info")
    assert get_run_id() != first  # each invocation gets a new id


def test_set_and_get_run_id_roundtrip() -> None:
    set_run_id("deadbeef")
    assert get_run_id() == "deadbeef"


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


def _record(message: str = "hello", level: int = logging.INFO, name: str = "scribeforge.test"):
    return logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=1, msg=message, args=(), exc_info=None
    )


def test_json_formatter_emits_one_parseable_object_per_line() -> None:
    formatter = JsonFormatter()
    line = formatter.format(_record("hello %s", logging.INFO))
    # args are interpolated into message
    record = logging.LogRecord(
        name="scribeforge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    payload = json.loads(formatter.format(record))
    assert set(payload) >= {"ts", "level", "logger", "run", "message"}
    assert payload["level"] == "INFO"
    assert payload["logger"] == "scribeforge.test"
    assert payload["message"] == "hello world"
    assert "\n" not in line


def test_json_formatter_includes_the_current_run_id() -> None:
    set_run_id("cafe1234")
    payload = json.loads(JsonFormatter().format(_record()))
    assert payload["run"] == "cafe1234"


def test_json_formatter_redacts_home_in_message(tmp_path: Path) -> None:
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    record = logging.LogRecord(
        name="scribeforge.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="cannot read %s",
        args=(str(home / "book.pdf"),),
        exc_info=None,
    )
    payload = json.loads(JsonFormatter(home=home).format(record))
    assert payload["message"] == "cannot read ~/book.pdf"
    assert str(home) not in json.dumps(payload)


def test_json_formatter_redacts_exception_text(tmp_path: Path) -> None:
    home = tmp_path / "home" / "alice"
    home.mkdir(parents=True)
    try:
        raise RuntimeError(f"boom at {home}/x")
    except RuntimeError:
        import sys

        exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="scribeforge.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failed",
        args=(),
        exc_info=exc_info,
    )
    payload = json.loads(JsonFormatter(home=home).format(record))
    assert "exception" in payload
    assert "boom at ~/x" in payload["exception"]
    assert str(home) not in payload["exception"]


def test_json_formatter_keeps_unicode_readable() -> None:
    record = logging.LogRecord(
        name="scribeforge.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="頁面訊息",
        args=(),
        exc_info=None,
    )
    line = JsonFormatter().format(record)
    assert "頁面訊息" in line  # ensure_ascii=False
    json.loads(line)  # still valid JSON


# ---------------------------------------------------------------------------
# secret-pattern redaction
# ---------------------------------------------------------------------------


def test_redact_text_masks_bearer_tokens() -> None:
    assert redact_text("auth: Bearer abcDEF123._-~+/=") == "auth: Bearer <redacted>"


def test_redact_text_masks_api_key_shapes() -> None:
    assert redact_text("key sk-abcdefghijklmnop1234 leaked") == "key <redacted> leaked"
    assert redact_text("ghp_abcdefghijklmnopqrstuvwxyz1234") == "<redacted>"


def test_redact_text_masks_key_value_credentials_but_keeps_the_key() -> None:
    assert redact_text("api_key=supersecret123") == "api_key=<redacted>"
    assert redact_text("password: hunter2") == "password: <redacted>"
    assert redact_text("client_secret = abcdef") == "client_secret = <redacted>"


def test_redact_text_leaves_ordinary_text_untouched() -> None:
    text = "processed 12 pages in 3.5s, password policy ok"
    assert redact_text(text) == text


# ---------------------------------------------------------------------------
# configure_logging with log_format
# ---------------------------------------------------------------------------


def test_configure_logging_json_uses_json_formatter_on_all_handlers(tmp_path: Path) -> None:
    log_file = tmp_path / "run.log"
    logger = configure_logging("debug", log_file, log_format="json")
    assert all(isinstance(h.formatter, JsonFormatter) for h in logger.handlers)
    assert len(logger.handlers) == 2  # console + rotating file

    get_logger("json-test").info("structured %s", "line")
    for handler in logger.handlers:
        handler.flush()
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        payload = json.loads(line)  # every line is valid JSON
        assert payload["message"] == "structured line"
        assert re.fullmatch(r"[0-9a-f]{8}", payload["run"])
    for handler in logger.handlers:
        handler.close()
        logger.removeHandler(handler)


def test_configure_logging_default_stays_text_format() -> None:
    logger = configure_logging("info")
    assert all(isinstance(h.formatter, RedactingFormatter) for h in logger.handlers)
    assert not any(isinstance(h.formatter, JsonFormatter) for h in logger.handlers)


def test_configure_logging_rejects_unknown_format() -> None:
    with pytest.raises(ValueError, match="unsupported log format"):
        configure_logging("info", log_format="yaml")


def test_configure_logging_json_keeps_console_at_warning(tmp_path: Path) -> None:
    logger = configure_logging("debug", tmp_path / "x.log", log_format="json")
    console, file_handler = logger.handlers
    assert console.level == logging.WARNING
    assert file_handler.level == logging.DEBUG
    for handler in logger.handlers:
        handler.close()
        logger.removeHandler(handler)


def test_text_format_line_shape_is_unchanged() -> None:
    formatter = RedactingFormatter()
    line = formatter.format(_record("plain message", logging.WARNING, "scribeforge"))
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} WARNING scribeforge: plain message$", line
    )


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_parser_has_log_format_defaulting_to_text() -> None:
    from scribeforge.cli import build_parser

    parser = build_parser()
    dests = {action.dest for action in parser._actions}
    assert "log_format" in dests
    args = parser.parse_args(["--log-format", "json", "status", "--store", "x"])
    assert args.log_format == "json"
    args = parser.parse_args(["status", "--store", "x"])
    assert args.log_format == "text"


def test_cli_rejects_unknown_log_format() -> None:
    from scribeforge.cli import build_parser

    with pytest.raises(SystemExit):
        build_parser().parse_args(["--log-format", "xml", "status", "--store", "x"])


def test_cli_log_format_json_writes_json_lines_with_run_id(tmp_path: Path) -> None:
    from scribeforge.cli import run

    log_file = tmp_path / "run.log"
    code = run(
        [
            "--log-level",
            "debug",
            "--log-format",
            "json",
            "--log-file",
            str(log_file),
            "status",
            "--store",
            str(tmp_path / "no-such-store"),
        ]
    )
    assert code != 0
    for handler in logging.getLogger("scribeforge").handlers:
        handler.flush()
    content = log_file.read_text(encoding="utf-8")
    assert content
    runs = set()
    for line in content.splitlines():
        payload = json.loads(line)
        assert {"ts", "level", "logger", "run", "message"} <= set(payload)
        runs.add(payload["run"])
    assert len(runs) == 1  # one run id per invocation
    for handler in logging.getLogger("scribeforge").handlers:
        handler.close()
        logging.getLogger("scribeforge").removeHandler(handler)


# ---------------------------------------------------------------------------
# run-tagged pipeline events
# ---------------------------------------------------------------------------


def test_event_line_includes_run_id_from_context() -> None:
    from scribeforge.pipeline.events import event_line

    set_run_id("run42abc")
    line = event_line("book_started", {"pages": 3}, now=1700000000.0)
    payload = json.loads(line)
    assert payload["event"] == "book_started"
    assert payload["pages"] == 3
    assert payload["run"] == "run42abc"
    assert payload["ts"] == 1700000000.0


def test_event_line_explicit_run_wins() -> None:
    from scribeforge.pipeline.events import event_line

    set_run_id("run42abc")
    payload = json.loads(event_line("page_started", {"page_index": 0}, now=1.0, run="explicit"))
    assert payload["run"] == "explicit"


def test_event_log_lines_carry_the_configured_run_id(tmp_path: Path) -> None:
    from scribeforge.pipeline.events import EventLog

    configure_logging("info")
    run_id = get_run_id()
    with EventLog(tmp_path / "events.jsonl") as log:
        log.emit("book_started", pages=2)
        log.emit("page_finished", page_index=0, elapsed_seconds=1.5)
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        assert json.loads(line)["run"] == run_id


def test_summarize_events_reports_the_run_id() -> None:
    from scribeforge.pipeline.events import event_line, summarize_events

    lines = [
        event_line("book_started", {"pages": 1}, now=1.0, run="run42abc"),
        event_line(
            "book_finished",
            {"succeeded": 1, "failed": 0, "elapsed_seconds": 2.0},
            now=3.0,
            run="run42abc",
        ),
    ]
    summary = summarize_events(lines)
    assert summary["run"] == "run42abc"
    assert summary["book_elapsed_seconds"] == 2.0


def test_summarize_events_run_is_none_without_run_field() -> None:
    from scribeforge.pipeline.events import summarize_events

    summary = summarize_events(['{"event": "book_started", "ts": 1.0}'])
    assert summary["run"] is None


def test_render_event_summary_shows_run_id_when_present() -> None:
    from scribeforge.pipeline.events import event_line, summarize_events
    from scribeforge.processing_report import render_event_summary

    lines = [
        event_line("book_started", {"pages": 1}, now=1.0, run="run42abc"),
        event_line("book_finished", {"succeeded": 1, "failed": 0, "elapsed_seconds": 2.0}, now=3.0),
    ]
    text = render_event_summary(summarize_events(lines))
    assert "- run: `run42abc`" in text
    # without a run field the line is omitted, not rendered as empty
    text = render_event_summary(summarize_events(['{"event": "book_started", "ts": 1.0}']))
    assert "- run:" not in text
