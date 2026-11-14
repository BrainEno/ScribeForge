# Logging

ScribeForge uses the standard-library `logging` module behind a shared
`scribeforge` logger (`src/scribeforge/observability.py`). Library code stays
silent unless the application configures it; the CLI configures it on every
invocation.

```bash
# Console shows warnings and errors only, so stdout/stderr contracts are undisturbed.
scribeforge status --store Book.scribeforge

# Raise verbosity and keep a rotating, redacted log file.
scribeforge --log-level debug --log-file run.log process book.pdf --store Book.scribeforge
```

- `--log-level` accepts `debug`, `info` (default), `warning`, `error`. The level
  applies to the logger and to the log file.
- `--log-file PATH` attaches a `RotatingFileHandler` (5 MiB per file, 3 backups).
  Parent directories are created as needed.
- `--log-format` selects the line format: `text` (default) or `json`.
- Every handler redacts before emitting: the user's home directory becomes `~`
  (tracebacks included), and common secret shapes are masked with `<redacted>`
  — `Bearer <token>` headers (scheme kept), well-known API-key prefixes
  (`sk-…`, `ghp_…`, `xoxb-…`, …), and `key: value` / `key=value` credentials
  (`api_key`, `password`, `client_secret`, …; the key name is kept, the value
  is masked). Log files are safe to attach to bug reports.
- `configure_logging()` is idempotent: repeated calls replace handlers instead of
  duplicating output.

## JSON log format and run ids

```bash
scribeforge --log-level debug --log-format json --log-file run.jsonl status --store Book.scribeforge
```

With `--log-format json` every line of every handler (console and rotating
file) is one JSON object with a stable schema:

```json
{"ts": "2026-11-05T10:00:00+0000", "level": "INFO", "logger": "scribeforge.cli", "run": "1a2b3c4d", "message": "..."}
```

`exception` is added when a record carries traceback info. Each CLI invocation
gets a fresh 8-hex-char `run` id (`observability.new_run_id`); the same id tags
every line of the invocation's JSONL pipeline event log (see below), so log
lines and events can be correlated across files. Redaction happens before JSON
encoding, so Windows paths never leak through backslash escaping.

## Structured pipeline events (JSONL)

## Structured pipeline events (JSONL)

For long book runs, `process --events-file PATH` writes one JSON object per
line (`src/scribeforge/pipeline/events.py`), independent of the text log:

```bash
scribeforge process book.pdf --store Book.scribeforge --events-file run.jsonl
```

Event kinds: `book_started` (pages, effective max_workers, gpu_memory_mib),
`page_started` / `page_finished` (page_index, elapsed_seconds),
`page_failed` (page_index, error, elapsed_seconds), and `book_finished`
(succeeded, failed, elapsed_seconds) or `book_aborted` on cancellation.
Every line also carries `run` — the invocation's run id, matching the `run`
field of `--log-format json` log lines. The file is truncated at the start of
each run, every line is redacted like the text log, and the sink is thread-safe
because page events are emitted from pool workers. `events` and `report
--events-file` summaries surface the run id as `run` when present.

`stage_finished` (stage, page_index, elapsed_seconds) is emitted for every
executed page stage, so per-engine timings are recoverable; skipped
(already-succeeded) stages emit nothing.

## Analyzing an event log

```bash
scribeforge events run.jsonl                 # Markdown bottleneck report
scribeforge events run.jsonl --json --top 20 # stable JSON, top-20 slowest pages
```

The report (`src/scribeforge/pipeline/event_analysis.py`) aggregates event
counts, per-stage totals/means with the bottleneck stage called out, the
slowest pages, failure reasons, in-flight pages at the end of the trace, and
book-level elapsed time. Malformed lines (a crash mid-write) are skipped.

Add `--estimate-remaining PAGES` to project the remaining wall time for the
unprocessed pages (observed mean page time divided by the run's worker count),
and `--plan` to also print a simulated wave schedule for the book's declared
page count (`scribeforge.domain.scheduling.plan_book_schedule`).
