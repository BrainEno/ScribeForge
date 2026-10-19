# ScribeForge

**Local-first, evidence-grade digitization for scanned literature.**

ScribeForge turns scanned books into high-confidence, traceable digital text and exports verified results to EPUB/DOCX.

The project is not trying to build yet another OCR engine. It orchestrates multiple OCR/document-analysis engines, detects disagreements, preserves page-level evidence, escalates only uncertain regions to a vision-language model or human review, and keeps every final decision traceable to the source scan.

## Core principles

- **Faithful transcription over fluent rewriting.** OCR and verification layers must never silently rewrite the source.
- **Evidence before confidence.** Every verified fragment must remain traceable to page coordinates and source imagery.
- **Multi-engine disagreement is a signal.** Independent OCR outputs are aligned and compared instead of blindly trusting one model.
- **Human review is exception-driven.** Users review unresolved/high-risk regions rather than rereading the whole book.
- **Local-first privacy.** Source books stay on the user's machine by default.
- **TDD by default.** Every behavior change starts with a failing test, followed by the smallest implementation that makes it pass.
- **Benchmark-driven architecture.** Real scanned-book benchmarks decide which model or pipeline stays.

## Initial target

First-class development target:

- Windows 11
- NVIDIA RTX 5080 / 16 GB VRAM
- 20 GB system RAM minimum for the reference machine
- SSD
- Chinese / English / Japanese scanned literature
- PDF, JPG and PNG inputs
- EPUB 3 and DOCX outputs

## Planned processing pipeline

```text
Import
  -> page decoding / preprocessing
  -> MinerU analysis
  -> PaddleOCR independent analysis
  -> spatial/text alignment
  -> disagreement + risk scoring
  -> cropped VLM verification for uncertain regions
  -> human verification queue
  -> verified structured document
  -> EPUB / DOCX export
```

## Repository structure

```text
src/scribeforge/       Python core
tests/                 unit / integration / contract tests
docs/                  architecture, TDD rules, data model, roadmap
benchmark/             reproducible benchmark fixtures and reports
.github/workflows/     CI enforcement
```

## Development status

The repository is in **Phase 2 — verification engine**, with the Phase 1 resumable CLI complete. Phase 0 (benchmark harness), Phase 0.5 (private runtime manager), the low-level verification path, the resumable CLI slices (PDF/image import, page-granular orchestration, runtime-backed workers, `scribeforge process`/`status`), and the decision layer (deterministic adjudication plus the human review queue) are implemented and covered by tests.

## CLI

```bash
scribeforge process book.pdf                  # import + process every page
scribeforge process book.pdf --store store/   # explicit project store
scribeforge status --store store/             # persisted jobs + review queue
scribeforge review --store store/             # pairs awaiting a human decision
scribeforge resolve --store store/ \
  --page 0 --pair 3 --text "鸦"              # record the accepted reading
```

`process` writes a project store beside the book by default (`<book-stem>.scribeforge`). Exit codes are semantic: `0` every page processed, `1` some pages failed (retryable), `2` fatal setup error (missing file, unsupported source, backend runtime not installed).

When two engines agree a decision is recorded as `automatic`; a confident VLM reading is recorded as `vlm`. Anything else stays in the review queue for a human decision — ScribeForge never rewrites source OCR. `review` prints each queued pair with both engine readings, the VLM reading (when one exists), its risk score/reasons, and the crop file to inspect; `resolve` appends a `human` decision that cites the verification run, both engine runs, and any VLM reading, superseding earlier automatic/VLM decisions on that pair without deleting them.

See:

- [AGENTS.md](./AGENTS.md)
- [Architecture](./docs/architecture.md)
- [Development plan](./docs/development-plan.md)
- [TDD policy](./docs/tdd.md)
- [Data model](./docs/data-model.md)

## Development bootstrap

Python 3.11 is the reference runtime.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
python -m pip install -U pip
pip install -e ".[dev]"
pytest
```

Model backends such as MinerU and PaddleOCR will be isolated behind adapters and may use separate environments/workers when dependency conflicts require it.

## Non-goals for the first milestone

Do not spend first-milestone effort on accounts, cloud sync, payments, collaboration, mobile apps, decorative animation, or general-purpose PDF tooling.

The first milestone exists to prove one thing:

> A difficult scanned literary book can be converted into structured text while preserving evidence and surfacing every unresolved uncertainty efficiently.
