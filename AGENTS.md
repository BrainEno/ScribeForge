# ScribeForge Agent Rules

These rules apply to all repository work.

## Product invariant
ScribeForge is an evidence-grade transcription system. Never silently “improve” source text. OCR, normalization, verification, and editorial text are separate layers. Every accepted OCR decision must remain traceable to source page coordinates and engine evidence.

## Development and testing
Choose the development approach that fits the change. Test-first is useful for
stable contracts and reproducible bugs; exploratory prototypes, UI iterations,
and refactors may start with implementation. Before marking work complete,
cover changed behavior and relevant failure paths with tests, and run the
focused tests followed by the applicable fast suite. Add regression coverage
for bugs; do not weaken a valid test merely to make CI green.

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
- tests cover the changed behavior and pass;
- type/lint checks pass (`ruff check .`, `ruff format --check .`, `mypy src`);
- no source evidence is overwritten;
- failure/retry behavior is defined;
- failure paths of I/O-heavy modules (backup/restore, runtime tools, hardware
  probe) are covered with injected fakes; the total coverage number must not
  dilute them — per-module floors are enforced in CI (see ci.yml);
- docs/schema are updated when contracts change;
- no fake implementation is presented as working OCR.

## Scope and initiative
Let the user's goal determine priorities and scope, not a fixed order of
subsystems. Implement coherent features across UI, CLI, runtime, and storage
when the end-to-end experience benefits. Product experiments and new features
are welcome; for changes that materially affect privacy, compatibility, or
stored data, make the impact explicit and define how to recover.

There is no repository-wide line-count target or requirement to minimize a
patch. Respect task-specific deliverables, and use the code needed to finish
them without padding diffs or adding unrelated work.
