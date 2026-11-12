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
- Every handler uses `RedactingFormatter`: the user's home directory is replaced
  with `~` in each rendered line (tracebacks included), so log files are safe to
  attach to bug reports.
- `configure_logging()` is idempotent: repeated calls replace handlers instead of
  duplicating output.

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
The file is truncated at the start of each run, every line is redacted like
the text log, and the sink is thread-safe because page events are emitted
from pool workers.
