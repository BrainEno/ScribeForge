# Development Plan

## Phase 0 — foundation and benchmark
- repository/TDD/CI foundation
- versioned engine-neutral OCR contracts
- SQLite provenance schema
- benchmark corpus manifest and scoring (CER/WER, missing/extra lines, punctuation, reading order)
- adapters for MinerU and PaddleOCR
- compare A: MinerU, B: Paddle, C: dual-engine diff, D: dual + VLM
- publish `benchmark_report.md` from real representative pages

## Phase 0.5 — private runtime manager
This phase is deliberately early: ordinary users must not be required to install Python, MinerU, PaddleOCR, CUDA toolkits, or virtual environments themselves.

- detect OS/architecture, NVIDIA GPU, driver, VRAM and RAM where available
- ship or resolve a private `uv` bootstrap executable
- install Python 3.12 into application-managed environments
- create isolated MinerU and PaddleOCR environments
- select a conservative CPU/GPU PaddlePaddle wheel from detected driver capability
- persist install/repair state and stop cleanly on a failed step
- centralize model/cache directories
- add backend health checks and repair/reinstall actions
- prefetch required OCR models before the first book is processed
- later surface all of this through a first-run wizard and Settings > Runtime & Models

**Gate:** OCR integration used by the product must run through application-managed runtimes rather than assuming developer-installed model stacks.

## Phase 1 — resumable CLI
`scribeforge process <book.pdf>`
Import pages incrementally, execute workers, persist each stage, resume interrupted work, retry failed pages.

## Phase 2 — verification engine
Spatial/text alignment, disagreement classification, risk scoring, cropped evidence generation, VLM adjudication and human decisions.

## Phase 3 — proofreading desktop UI
Library, job progress, source/text synchronized viewer, conflict queue, keyboard-first review, settings. The first-run runtime wizard is exposed here, backed by the already-tested Phase 0.5 manager.

## Phase 4 — export
EPUB 3 + DOCX generated from verified structured content, validation tests and metadata editor.

## Later
Speech-to-text and text-to-speech may join the same text library only after book digitization is reliable.
