# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Benchmark regression comparison (issues #62/#68/#90):
  `scribeforge.benchmark.regression` compares two schema-v1
  `benchmark --report-json` payloads metric by metric — aggregate,
  per-language, per-direction, vertical reading order and per-case — and
  renders a Markdown or schema-v1 JSON report. The new `benchmark-compare`
  subcommand adds a CI gate (`--fail-on-regression`, exit 3) with
  configurable CER/WER/reading-order thresholds in percentage points
  (`--cer-pp`/`--wer-pp`/`--ro-pp`). N/A metrics (e.g. WER for zh/ja) stay
  null and are never treated as 0 or as regressions.
- Synthetic multilingual benchmark corpus (issues #62/#68):
  `scribeforge.benchmark.multilingual_synthetic.build_multilingual_corpus`
  generates a deterministic zh/en/ja fixture corpus (English two-column page,
  Japanese vertical-rl pages, CC0-1.0) with an oracle binding
  (`oracle_for_manifest`), so the per-language / per-direction / vertical
  reading-order report and JSON strata are exercised in CI even before real
  en/ja scans land. A scripted column-swapping engine proves the vertical
  reading-order error rate fires. Synthetic pages are pipeline-mechanics
  fixtures, never OCR accuracy samples.
- License documentation guard (issue #90): tests pin that every backend in
  `build_runtime_plan` is named in `docs/licenses.md`, optional backends are
  marked optional there, and non-Apache weights (Surya: OpenRAIL-M) are called
  out.

- Corpus annotation tooling (issues #62/#68): `scribeforge.benchmark.annotation`
  adds per-page spec overrides (`load_page_spec` / `resolve_case_metadata`) so
  one PDF can yield a multilingual corpus, and an annotation-status report
  (`annotation_status`, Markdown + schema-v1 JSON) showing per-language and
  per-direction coverage, empty/missing ground truths and the quality-gate
  verdict. `scribeforge benchmark --annotation-status [--quality-json]` writes
  it to `--out` and exits 2 while annotations are incomplete;
  `benchmark/analysis/build_corpus.py --page-spec spec.json` applies the
  per-page overrides when building a corpus.

- Technology-route backends are first-class by name (issue #90): `setup
  --json --backend paddleocr-vl|surya` repairs the named optional backend,
  `runtime-status` (Markdown/JSON) and the final `setup --json` record report
  all four backends with an `optional` flag, and `RuntimeOverview` gained
  `optional_backends`. `summarize_engine_votes` / `EngineVoteSummary` expose
  the per-pair vote distribution (cluster sizes, winner, unanimous/majority/
  split) and every `PairAdjudication` carries it; `adjudicate_readings` remains
  the single N-ary rule, pinned by an exhaustive truth-table test (448 cells
  for 3 engines, 1792 for 4). See `docs/multi-engine-adjudication.md`.

- Processing report engine timing (issue #69): `EngineTiming` aggregates each
  OCR engine's latest-attempt seconds, share of all measured job time and mean
  per timed job (unmeasured stays `None`, never zero). The Markdown report
  gains an `Engine timing` section, a bottleneck-stage header line and a
  `Slowest pages` section (`report --top N` controls the limit, default 10);
  the schema-v1 JSON gains `timing.engines`, `timing.bottleneck_stage` and
  `slowest_pages`. `report --compare` also renders per-engine timing deltas,
  and `status` (Markdown and JSON) shows observed job time, the bottleneck
  stage and per-engine seconds per project.

- Multilingual benchmark reporting (issue #68): `reading_order_error_rate`
  (inversions per reference line) is computed per case and per aggregate and
  rendered as `RO err rate` in the variants, per-case, per-language and
  per-direction tables. The `Results by language` table is mandatory and
  carries reading-order columns; a new `Vertical text reading order` section
  reports the vertical reading-order error rate separately, and the writing
  direction table renders whenever any case declares a `direction`.
  `benchmark/manifest.example.json` now models an English two-column case and
  a Japanese vertical case, and README.md marks English/Japanese support as
  pending verification instead of a bare claim. `scribeforge benchmark
  --report-json PATH` writes the run as schema-v1 JSON with mandatory
  per-language strata and vertical reading-order rates.

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
