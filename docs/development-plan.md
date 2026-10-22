# Development Plan

## Phase 0 — foundation and benchmark
- repository/TDD/CI foundation
- versioned engine-neutral OCR contracts
- SQLite provenance schema
- benchmark corpus manifest and scoring (CER/WER, missing/extra lines, punctuation, reading order)
- adapters for MinerU and PaddleOCR
- compare A: MinerU, B: Paddle, C: dual-engine diff, D: dual + VLM
- publish `benchmark_report.md` from real representative pages

Status: `scribeforge benchmark` now validates a corpus, builds only the requested managed backends, scores variants A (MinerU) and B (PaddleOCR) with per-page timing, and writes a deterministic Markdown report naming the manifest SHA-256, with per-case rows (language, tags) next to the aggregate table. The remaining Phase 0 work is the pipeline-level comparison (C and D) and the real run: `benchmark_report.md` stays pending until it is generated from legally usable pages with hand-verified ground truth and installed runtimes.

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
- expose all of this through the standalone first-run wizard; a persistent Settings > Runtime & Models entry in the review shell remains

**Gate:** OCR integration used by the product must run through application-managed runtimes rather than assuming developer-installed model stacks.

## Phase 1 — resumable CLI
`scribeforge process <book.pdf>`
Import pages incrementally, execute workers, persist each stage, resume interrupted work, retry failed pages.

Phase 1 foundation includes schema-v4 resumable jobs with project/page scope, attempts, failure details, and durable pending/running/failed state.

The import slice is connected: `PageSource` (domain contract) + `PdfPageSource`/`ImagePageSource` adapters (pdfium/Pillow) + `import_source` orchestration materialize immutable per-page raster assets, persist page evidence, and drive the project-scoped `import` job resumably.

The page-granular orchestrator is connected: `process_page`/`process_book` drive PRIMARY_OCR -> SECONDARY_OCR -> VERIFY -> VLM_REVIEW per page through the job state machine, injecting engines and reviewers, persisting each stage before releasing page resources, skipping already-succeeded stages, resuming interrupted ones, and isolating a failed page from the rest of the book.

Worker execution now runs through application-managed runtimes: `build_runtime_engine` resolves a backend's managed Python via `RuntimeLayout`, centralizes model/cache directories, and enforces a health check at construction and before every page so a runtime removed mid-book fails loudly.

The CLI is wired: `scribeforge process <book>` resolves a per-book store (default `<book>.scribeforge`), creates or reuses the project, imports pages, builds runtime-backed engines, and drives the page-granular orchestrator; `scribeforge status --store <dir>` reports persisted jobs. Exit codes are semantic (`0` full success, `1` partial/failed pages, `2` fatal setup/usage error). When no VLM adjudicator is configured the review stage is left unstarted so a later run can still perform it. Phase 1 is complete.

## Phase 2 — verification engine
Spatial/text alignment, disagreement classification, risk scoring, cropped evidence generation, VLM adjudication and human decisions.

The lower-level verification path already has dual-engine alignment/risk scoring, deterministic review crop planning/materialization, literal VLM worker contracts, append-only VLM evidence, and pair-level resume semantics.

The decision layer is now wired: `adjudicate_verification` turns each verified pair into an append-only decision. Independent engine agreement is accepted as `automatic`, a confident non-blank VLM reading is accepted as `vlm`, and anything else stays unresolved for human review (`review_queue`). Every decision cites the verification run, both OCR engine runs, and the VLM reading when one was used; source OCR is never rewritten. The orchestrator runs this per page after VLM review, and `scribeforge process`/`status` surface the decision counts and the remaining review queue.

The human loop is now wired end to end: `review_detail` loads the competing evidence for one queued pair (both engine readings, the newest VLM reading, risk score/reasons, the materialized crop path when the crop exists, and the currently accepted text), and `record_human_decision` appends a `human` decision citing that same evidence. `scribeforge review --store <dir>` prints the queued pairs, and `scribeforge resolve --store <dir> --page N --pair N --text <literal>` records the accepted reading; a human decision becomes the pair's newest decision, clears it from the review queue, and survives re-adjudication while the superseded row remains as evidence. Source OCR is never rewritten, and a human may also correct an earlier `automatic`/`vlm` decision on a pair.

## Phase 3 — proofreading desktop UI
Library, job progress, source/text synchronized viewer, conflict queue, keyboard-first review, settings. The first-run runtime wizard is exposed here, backed by the already-tested Phase 0.5 manager.

The desktop shell now has keyboard-first human decision entry: `scribeforge ui --store <dir>` opens a desktop window with the library (title, page count, queued pairs, failed jobs), the persisted job list, the review queue, and a pair view with an evidence tab and image tabs for the immutable page raster and the review crop (display-only previews through `scribeforge.adapters.image_preview`). Presentation logic lives in `scribeforge.ui.viewmodel`, which imports no GUI framework and is unit tested against a real `SQLiteStore`; `scribeforge.ui.tk_window` is the Tkinter widget layer, imported lazily so other commands work without Tkinter or a display and excluded from coverage (headless CI). The selected conflict now synchronizes to the page raster: selecting a queue row
switches to the source page with the candidate region highlighted, and explicit
shortcuts focus the primary or secondary OCR line geometry on a transient preview.
No source asset or evidence is modified. A side-by-side character-diff tab renders the persisted primary/secondary alignment ranges with distinct marks for substitution, insertion, deletion, and punctuation, including visible empty-side markers. Clicking a marked conflict selects it in both panes and focuses the corresponding engine's OCR line on the source-page preview. The source outline is deliberately line-level because persisted geometry is line-level; the UI does not imply pixel-accurate character boxes. A richer synchronized transcript/image editing surface and an in-shell Settings > Runtime & Models entry remain. The standalone `scribeforge setup` wizard now detects hardware, shows persisted backend states, downloads and verifies the private `uv` bootstrap when needed, installs models in a background worker, and offers per-backend repair.

A Flutter desktop comparison client now lives under `clients/flutter_desktop`. It opens a source PDF and two EPUB candidates in a three-pane view, aligns source page N with the candidate spine item N (with an explicit warning when spine names do not encode source pages), highlights line-level disagreements, and saves per-page and whole-book human evaluation metadata beside the source PDF. The Python `export-candidates` command creates clearly marked unverified candidates from persisted engine output; the verified EPUB/DOCX export path remains separate. Flutter has its own dependency manifest, desktop bootstrap scripts, unit/widget tests, and a dedicated GitHub Actions job. This slice compares extracted text and provides human judgment; it does not yet render every source EPUB's CSS faithfully or invoke OCR engines directly from the Flutter process, so candidates are prepared from the Python pipeline first.

## Phase 4 — export
EPUB 3 + DOCX generated from verified structured content, validation tests and metadata editor.

EPUB 3 export is now wired: `assemble_verified_content` composes a `VerifiedBook` from the newest accepted decision for every pair, refusing to assemble while any page is unverified or any pair is unresolved. `scribeforge.adapters.epub` renders that book as a deterministic EPUB 3 archive (stored `mimetype`, container, package, nav, one XHTML per page) with XML-escaped text, and `write_epub` replaces the destination atomically. `scribeforge.adapters.docx` writes the same book as a deterministic WordprocessingML package (`[Content_Types].xml`, package relationships, `word/document.xml`, `docProps/core.xml`), one paragraph per accepted line and an explicit page break between pages; both adapters share fixed zip timestamps, atomic replacement, and metadata validation through `scribeforge.adapters.export_common`. `scribeforge export --store <dir> --out <file> [--language <tag>]` selects the format from the extension and exits `2` rather than exporting a partial book. Export metadata is overridable: `--title`, `--author` (written as `dc:creator` in both formats), and `--modified` (ISO 8601, naive values treated as UTC) for byte-reproducible builds. Both exporters reject characters XML 1.0 cannot represent, naming the offending page and line, instead of writing an archive no reader can open. Each build is then re-read by `scribeforge.adapters.export_check`, which verifies archive structure, manifest/relationship targets, spine order, and that every page reproduces its accepted lines exactly. The desktop metadata editor remains.

## Later
Speech-to-text and text-to-speech may join the same text library only after book digitization is reliable.
