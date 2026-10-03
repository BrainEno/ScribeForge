# Development Plan

## Phase 0 — foundation and benchmark
- repository/TDD/CI foundation
- versioned engine-neutral OCR contracts
- SQLite provenance schema
- benchmark corpus manifest and scoring (CER/WER, missing/extra lines, punctuation, reading order)
- adapters for MinerU and PaddleOCR
- compare A: MinerU, B: Paddle, C: dual-engine diff, D: dual + VLM
- publish `benchmark_report.md` from real representative pages

**Gate:** do not commit to a GUI architecture until the core pipeline has evidence from a real benchmark.

## Phase 1 — resumable CLI
`scribeforge process <book.pdf>`
Import pages incrementally, execute workers, persist each stage, resume interrupted work, retry failed pages.

## Phase 2 — verification engine
Spatial/text alignment, disagreement classification, risk scoring, cropped evidence generation, VLM adjudication and human decisions.

## Phase 3 — proofreading desktop UI
Library, job progress, source/text synchronized viewer, conflict queue, keyboard-first review, settings.

## Phase 4 — export
EPUB 3 + DOCX generated from verified structured content, validation tests and metadata editor.

## Phase 5 — model manager
One-click backend installation/configuration, shared cache directory, hardware detection and safe batch sizing.

## Later
Speech-to-text and text-to-speech may join the same text library only after book digitization is reliable.
