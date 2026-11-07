# Processing report

`scribeforge report` turns an existing project store into a deterministic Markdown
or JSON summary. It uses persisted jobs, latest verifications, decisions and the
production review queue; it does not run OCR, change decisions, rewrite source
images or synthesize missing measurements.

```bash
# A store containing one project selects it automatically.
scribeforge report --store Book.scribeforge

# Select a project explicitly in a multi-project store.
scribeforge report --store shared.scribeforge --project 2

# Stable schema-v1 JSON for automation.
scribeforge report --store Book.scribeforge --json
```

Exit code `0` means the report was generated, **not** that every page succeeded.
A missing store, ambiguous project selection or unsafe historical decision binding
returns `2`. Page/job failures remain visible in the report itself.

## Included evidence

- total, OCR-complete, failed-job and running-job page counts;
- per-stage pending/running/succeeded/failed counts and attempts, with primary and
  secondary OCR kept as separate rows;
- per-page primary/secondary/latest-attempt timestamp spans and total observed
  job spans;
- accepted automatic/VLM/human decisions, open/high-risk/blocked review pairs and
  the open-review ratio;
- global and per-page risk-reason occurrence counts from latest verifications.

The JSON top-level fields are `schema_version`, `project_id`, `title`, page counts,
`review`, `risk_reasons`, `timing`, `stages`, and `pages`. Page indexes remain
zero-based in JSON; Markdown labels them as one-based. Output has no generated
timestamp, so identical stored evidence renders identical content. Source URIs,
job error strings and crop paths are deliberately not included.

## Timing semantics — important

A job row stores only its **latest** `started_at` / `finished_at` pair. The report
therefore calls these numbers *observed timestamp spans*, not complete engine cost.
Only succeeded/failed jobs with a complete, valid, non-negative interval are timed.
Pending/running jobs, earlier retries, missing/malformed timestamps and negative
spans are counted as unavailable and render as `—`, never as invented zero-second
measurements. Normal SQLite timestamps have whole-second resolution.

`timing.scope` is `latest_attempt_timestamp_spans`. `observed_seconds` is the sum
of available job spans; it is not CPU time and not total book wall time. Stage
attempt counts still show retries even though earlier attempt durations are not
recoverable from the current schema.

## Review and risk semantics

Review counts use the production queue with page furniture excluded, matching
`status`, `repair` and normal review behavior. The ratio is `open_pairs / pairs`,
or `0` for a project with no measured pairs. Risk-reason counts include occurrences
on latest verification pairs; one pair can contribute to several reasons. These
are diagnostic distributions, not accuracy estimates or proof that accepted OCR
text is correct.

## Deliberate remaining work

This slice does not add a rotating/redacting logger, per-attempt timing history,
real-time streaming, or a Flutter progress/risk visualization. Those require
separate schema, UX and privacy work; the report does not claim to complete all of
issue #69.
