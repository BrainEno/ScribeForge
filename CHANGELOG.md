# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
