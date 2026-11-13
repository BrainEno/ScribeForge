# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Benchmark manifests now carry `license` / `license_url` at top level and
  `source` per case (issue #62, provenance for redistributable corpora). The
  quality gate fails on a missing license and warns on non-standard license
  strings or missing sources; benchmark reports render the corpus license.

- Scheduling estimators (issue #70): `estimate_remaining_seconds` and
  `plan_book_schedule` in `scribeforge.domain.scheduling` project remaining
  wall time and a wave schedule from observed per-page seconds; exposed as
  `scribeforge events <log> --estimate-remaining PAGES [--plan]`. Opt-in
  performance baseline `tests/test_parallel_performance.py` (benchmark marker)
  proves page parallelism exceeds 2x over serial on a 120-page fake book.

- Processing reports now include a risk-score distribution (5 equal-width
  buckets over all latest-verification pair scores, with high-risk totals) in
  Markdown and schema-v1 JSON, and `scribeforge report --compare old.json`
  renders field-level deltas between two exported reports (issue #69).

- Event-log analysis (issue #69): `process --events-file` traces now include
  per-stage `stage_finished` events, and the new `scribeforge events <file>
  [--json] [--top N]` command renders a bottleneck report (per-stage
  totals/means with the slowest engine called out, slowest pages, failure
  reasons, book elapsed) via `scribeforge.pipeline.event_analysis`.

- Structured JSONL pipeline events (issue #69): `process --events-file PATH`
  writes `book_started` / `page_started` / `page_finished` / `page_failed` /
  `book_finished` / `book_aborted` lines with page-level elapsed seconds and
  book totals. New `scribeforge.pipeline.events` module; the sink is
  thread-safe (page events come from pool workers) and home-redacted.

- Benchmark corpus quality gate (issues #62/#68): new
  `scribeforge.benchmark.quality_gate` module checks a manifest for minimum
  size, per-language coverage (zh/en/ja >= 8 pages each), vertical Japanese and
  two-column English samples, duplicate images and empty ground truth. Run via
  `scribeforge benchmark --check-only` (Markdown or `--quality-json` report,
  exit 2 on blocking issues); rendered benchmark reports embed the same
  coverage warnings.

- GPU memory-budget scheduling for page parallelism (issue #70):
  `process --gpu-memory-mib <MiB>` caps the page worker count so in-flight
  pages fit the budget (`min(--workers, budget // per-page VRAM)`); engines may
  declare their footprint via `vram_mib`, with a conservative per-page default.
  New `scribeforge.domain.scheduling` module with pure, exhaustively tested
  resolution rules.
- Page-level parallel processing: `process --workers <n>` (default `0` = half
  the CPU cores, `1` = serial) runs independent pages on a bounded thread pool
  (issue #70). Job writer locks are now page-granular, so concurrent pages of
  one project no longer conflict while the same page still conflicts across
  processes; book-level stages keep the project-wide lock.

## [0.1.0] - 2026-11-11

First tagged release. ScribeForge is a local-first, evidence-grade
digitization pipeline for scanned literature: dual-engine OCR
(MinerU + PaddleOCR) with per-page disagreement risk scoring, an
evidence-backed human review queue, and verified EPUB 3 / DOCX export.

### Added

- Resumable `process` pipeline (import, preprocess, dual OCR, alignment,
  risk scoring, optional VLM review) with cancellable, retryable jobs.
- Trusted review queue with immutable decision log (`resolve`/`confirm`)
  and restart-safe persistence in a per-book SQLite store.
- Candidate comparison (A/B/Tie ratings) stored in a SHA-256-bound
  sidecar, separate from accepted text.
- Verified export to EPUB 3 and DOCX with a strict acceptance gate.
- Project safety commands: read-only `audit`, hashed `backup`,
  `verify-backup`, and restore-only-to-new-directory.
- Flutter desktop client with one-click backend provisioning, a
  next-step guidance banner, resume-last-project, and a Chinese help
  dialog.
- Apache-2.0 license (see [LICENSE](LICENSE)); dependency license inventory in
  [docs/licenses.md](docs/licenses.md) (Surya weights are OpenRAIL-M, not Apache-2.0).

[0.1.0]: https://github.com/BrainEno/ScribeForge/releases/tag/v0.1.0
