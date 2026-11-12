"""Structured JSONL pipeline events (issue #69).

A multi-hour book run should leave a machine-readable trace of where the time
went. :class:`EventLog` appends one JSON object per line for every milestone
(``book_started``, ``page_started``, ``page_finished``, ``page_failed``,
``book_finished``, ``book_aborted``); :func:`event_line` builds a single line
and is pure, so the wire format is unit-testable without touching the disk.

Home-directory paths are redacted from every line (via
:func:`scribeforge.observability.redact_text`) so event files stay safe to
share. The log is thread-safe: page-level events are emitted from pool worker
threads.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scribeforge.observability import redact_text


def event_line(
    event: str, fields: dict[str, Any] | None = None, *, now: float | None = None
) -> str:
    """Build one JSONL line for ``event``; pure and deterministic when ``now`` is given.

    String field values are redacted *before* JSON encoding: on Windows the
    encoder escapes backslashes, so redacting the finished line would miss
    home paths like ``C:\\Users\\...``. The finished line is redacted too, as a
    belt-and-braces pass for anything that slipped through.
    """
    payload: dict[str, Any] = {
        "ts": round(time.time() if now is None else now, 3),
        "event": event,
    }
    if fields:
        payload.update(
            {
                key: redact_text(value) if isinstance(value, str) else value
                for key, value in fields.items()
            }
        )
    return redact_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


class EventLog:
    """Append-only JSONL event sink; safe to share across worker threads."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._closed = False
        # Truncate at open: each run starts a fresh trace.
        self._path.write_text("", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    def emit(self, event: str, **fields: Any) -> None:
        """Append one event line; silently ignored after :meth:`close`."""
        line = event_line(event, fields)
        with self._lock:
            if self._closed:
                return
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def __enter__(self) -> EventLog:  # noqa: PYI034 - typing_extensions is dev-only; 3.10-safe
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def summarize_events(lines: Sequence[str]) -> dict[str, Any]:
    """Aggregate raw JSONL event lines into a small summary dict.

    Pure function over already-read lines: counts per event kind, total book
    elapsed seconds from ``book_finished``/``book_aborted``, the failed page
    indexes, and the slowest page. Missing or malformed lines are skipped, so
    a truncated tail (crash mid-write) never breaks the summary.
    """
    counts: dict[str, int] = {}
    failed_pages: list[int] = []
    book_elapsed: float | None = None
    slowest: tuple[int, float] | None = None
    total_page_seconds = 0.0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = payload.get("event")
        if not isinstance(event, str):
            continue
        counts[event] = counts.get(event, 0) + 1
        if event == "page_failed" and isinstance(payload.get("page_index"), int):
            failed_pages.append(payload["page_index"])
        if event in ("book_finished", "book_aborted") and isinstance(
            payload.get("elapsed_seconds"), (int, float)
        ):
            book_elapsed = float(payload["elapsed_seconds"])
        if event == "page_finished" and isinstance(payload.get("page_index"), int):
            elapsed = payload.get("elapsed_seconds")
            if isinstance(elapsed, (int, float)):
                total_page_seconds += float(elapsed)
                if slowest is None or float(elapsed) > slowest[1]:
                    slowest = (payload["page_index"], float(elapsed))
    return {
        "events": counts,
        "failed_pages": sorted(failed_pages),
        "book_elapsed_seconds": book_elapsed,
        "total_page_seconds": round(total_page_seconds, 6),
        "slowest_page": {"page_index": slowest[0], "elapsed_seconds": slowest[1]}
        if slowest
        else None,
    }
