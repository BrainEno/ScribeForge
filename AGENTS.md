# ScribeForge Agent Rules

These rules apply to all repository work.

## Product invariant
ScribeForge is an evidence-grade transcription system. Never silently “improve” source text. OCR, normalization, verification, and editorial text are separate layers. Every accepted OCR decision must remain traceable to source page coordinates and engine evidence.

## Mandatory TDD
Use Red -> Green -> Refactor for every behavior change:
1. Write or update a test that expresses the desired behavior.
2. Run it and confirm it fails for the expected reason.
3. Implement the smallest production change that passes.
4. Run the focused test, then the full fast suite.
5. Refactor only while tests stay green.

Bug fixes require a regression test reproducing the bug first. Do not weaken/delete a valid test merely to make CI green.

## Test boundaries
- Unit tests: pure domain logic, alignment, risk, normalization, storage rules.
- Contract tests: every OCR/VLM adapter against stable local fixtures; no model download in default CI.
- Integration tests: database + pipeline orchestration with deterministic fake engines.
- Benchmark tests: real representative scans, explicitly opt-in and never confused with unit tests.
- Export tests: EPUB/DOCX structure and validation.
- GPU/model tests are opt-in markers and must not block ordinary contributor CI.

## Architecture
Keep model runtimes behind adapters. Core domain code must not import MinerU, PaddleOCR, Surya, CUDA, Torch, or GUI frameworks. Prefer subprocess/separate environments when model dependencies conflict.

Pipeline stages must be restartable and page-granular. Persist results before releasing page resources. A failed page must not invalidate a whole book.

## Definition of done
A slice is done only when:
- tests were added first and now pass;
- type/lint checks pass;
- no source evidence is overwritten;
- failure/retry behavior is defined;
- docs/schema are updated when contracts change;
- no fake implementation is presented as working OCR.

## Scope discipline
Current priority: benchmark -> core contracts -> persistence -> adapters -> alignment/risk -> verification -> export -> desktop UI.
Do not add accounts, cloud sync, mobile apps, payments, collaboration, or decorative UI before the core pipeline is proven.
