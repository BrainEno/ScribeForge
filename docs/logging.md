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

### Diagnosing and retrying individual pages

Before retrying, preserve a copy of the entire project store and any existing
logs. `scribeforge status --store PROJECT.scribeforge --json` reports recent
failed jobs, their attempts, and `last_error`; it lists at most 20 failures.
Starting a job clears its previous error, so capture this output first. Logs and
errors can contain private paths, book content, or credentials despite
best-effort redaction; inspect them before sharing. Older versions may have
retained only the worker exit status, not the original stderr.

For an existing, fully imported single-project store, a targeted resume can be
run with `scribeforge process ORIGINAL.pdf --store PROJECT.scribeforge
--page-index 17 --page-index 373 --workers 1`. Indices are **zero-based physical
pages**, not printed page numbers: 17 and 373 correspond to pages 18 and 374
only if those numbers refer to the PDF's physical pages. Verify this mapping
first. Use the original source and the same preprocessing settings as the
initial run; mismatched frames are rejected. Completed stages are reused subject
to normal evidence checks, while unfinished stages on the selected pages resume.
The command does not rebuild book-wide structure or run repair rounds. It can
still create OCR/review evidence for those pages; it is not a read-only diagnosis.
If `--events-file` is used for a scoped run, choose a new filename; existing
files are rejected. Keep the pre-retry backup until the result is reviewed.

New worker failures include a bounded, best-effort-redacted stderr tail in the
job error; empty stderr and output-limit failures may still give no traceback.
This does not recover stderr already lost by an older version or establish the
cause of a failure without the actual project diagnostics.

### Large books and resumability

Import and page stages persist progress. If the process is stopped or the machine
restarts, keep the same project directory and original source, then start process
again. Import resumes missing pages, successful OCR stages are reused, and
failed/in-flight stages can retry. Always capture status and back up the project
first: restarting a failed job clears its previous `last_error`. No retry can
reconstruct stderr already lost by an earlier version. Source identity, raster
and preprocessing-frame checks remain in force; do not replace original files or
change preprocessing settings on an existing project.

The desktop's optional page concurrency runs up to four pages in parallel. The
default is one to protect RAM and GPU memory. Raising it starts independent OCR
workers and may consume much more RAM/VRAM, run slower, or fail on constrained
machines; it is not a CPU-only mode and does not promise a speedup. Measure on the
actual hardware and fall back to one page if necessary. Successful page stages
remain resumable if a parallel run fails.
