# Development Plan

## Phase 0 — foundation and benchmark
- repository/TDD/CI foundation
- versioned engine-neutral OCR contracts
- SQLite provenance schema
- benchmark corpus manifest and scoring (CER/WER, missing/extra lines, punctuation, reading order)
- adapters for MinerU and PaddleOCR
- compare A: MinerU, B: Paddle, C: dual-engine diff, D: dual + VLM
- publish `benchmark_report.md` from real representative pages

Status: `scribeforge benchmark` now validates a corpus, builds only the requested managed backends, scores variants A (MinerU) and B (PaddleOCR) with per-page timing, and writes a deterministic Markdown report naming the manifest SHA-256, with per-case rows (language, tags) next to the aggregate table. Variant C is now runnable with `scribeforge benchmark --include-pipeline`: A/B engine scores are supplemented by the actual dual-engine alignment, risk and decision stages in an isolated temporary SQLite store. Only accepted decisions are scored; unresolved pairs are omitted rather than guessed and are reported as coverage counts. Variant D is runnable through an OpenAI-compatible vision chat endpoint configured with --include-pipeline --include-vlm --vlm-endpoint <loopback-url> --vlm-model <model>. Only materialized review crops are sent; non-loopback endpoints are refused unless --allow-remote-vlm is explicit. ScribeForge does not download or silently start a VLM server, so lifecycle management and a bundled/model-managed runtime remain future work. The standalone `scribeforge vlm-check --image <crop> --vlm-endpoint <loopback-url> --vlm-model <model>` command provides a one-image, no-database preflight; its output is diagnostic only and blank/uncertain results do not count as success. The real run remains pending: `benchmark_report.md` must be generated from legally usable pages with hand-verified ground truth and installed runtimes.

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
- expose all of this through the standalone first-run wizard; the persistent Settings > Runtime & Models entry is implemented in the Flutter review shell

**Gate:** OCR integration used by the product must run through application-managed runtimes rather than assuming developer-installed model stacks.

## Phase 1 — resumable CLI
`scribeforge process <book.pdf>`
Import pages incrementally, execute workers, persist each stage, resume interrupted work, retry failed pages.

Phase 1 foundation includes schema-v4 resumable jobs with project/page scope, attempts, failure details, and durable pending/running/failed state.

The import slice is connected: `PageSource` (domain contract) + `PdfPageSource`/`ImagePageSource` adapters (pdfium/Pillow) + `import_source` orchestration materialize immutable per-page raster assets, persist page evidence, and drive the project-scoped `import` job resumably.

The page-granular orchestrator is connected: `process_page`/`process_book` drive PRIMARY_OCR -> SECONDARY_OCR -> VERIFY -> VLM_REVIEW per page through the job state machine, injecting engines and reviewers, persisting each stage before releasing page resources, skipping already-succeeded stages, resuming interrupted ones, and isolating a failed page from the rest of the book.

Worker execution now runs through application-managed runtimes: `build_runtime_engine` resolves a backend's managed Python via `RuntimeLayout`, centralizes model/cache directories, and enforces a health check at construction and before every page so a runtime removed mid-book fails loudly.

The CLI is wired: `scribeforge process <book>` resolves a per-book store (default `<book>.scribeforge`), creates or reuses the project, imports pages, builds runtime-backed engines, and drives the page-granular orchestrator; `scribeforge status --store <dir>` reports persisted jobs. Exit codes are semantic (`0` full success, `1` partial/failed pages, `2` fatal setup/usage error). An optional OpenAI-compatible VLM reviewer can be configured with --vlm-endpoint and --vlm-model; it receives only materialized conflict crops, remote endpoints require HTTPS and explicit --allow-remote-vlm opt-in, and uncertain or malformed replies remain unresolved. When no reviewer is configured the review stage is left unstarted so a later run can still perform it. Phase 1 is complete.

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
No source asset or evidence is modified. A side-by-side character-diff tab renders the persisted primary/secondary alignment ranges with distinct marks for substitution, insertion, deletion, and punctuation, including visible empty-side markers. Clicking a marked conflict selects it in both panes and focuses the corresponding engine's OCR line on the source-page preview. The source outline is deliberately line-level because persisted geometry is line-level; the UI does not imply pixel-accurate character boxes. A richer synchronized transcript/image editing surface remains. The persistent in-shell Settings > Runtime & Models entry is implemented and exposes the read-only runtime-status contract. The standalone `scribeforge setup` wizard now detects hardware, shows persisted backend states, downloads and verifies the private `uv` bootstrap when needed, installs models in a background worker, and offers per-backend repair.

A Flutter desktop comparison client now lives under `clients/flutter_desktop`. It opens a source PDF and two EPUB candidates in a three-pane view, aligns source page N with the candidate spine item N (with an explicit warning when spine names do not encode source pages), highlights line-level disagreements, and saves per-page and whole-book human evaluation metadata beside the source PDF. The Python `export-candidates` command creates clearly marked unverified candidates from persisted engine output; the verified EPUB/DOCX export path remains separate. Flutter has its own dependency manifest, desktop bootstrap scripts, unit/widget tests, and a dedicated GitHub Actions job. This slice compares extracted text and provides human judgment; it does not yet render every source EPUB's CSS faithfully or invoke OCR engines directly from the Flutter process, so candidates are prepared from the Python pipeline first. The CLI also exposes a read-only `scribeforge audit --store <folder> --json` command, available from the Flutter workspace, to inspect SQLite integrity, foreign-key consistency, source SHA-256, and the presence of imported page images before users resume or archive a project. The project backup slice now creates a hashed ZIP containing a SQLite online-backup snapshot, all regular files in the store, and verified original source documents. `verify-backup` checks safe archive paths, the manifest, every file hash, and SQLite integrity; `restore` validates first, stages extraction in the destination filesystem, relocates internal page/source paths, and publishes only to a new destination. Backup is refused while jobs are running or when required source identity/page evidence is invalid.

## Phase 4 — export
EPUB 3 + DOCX generated from verified structured content, validation tests and metadata editor.

EPUB 3 export is now wired: `assemble_verified_content` composes a `VerifiedBook` from the newest accepted decision for every pair, refusing to assemble while any page is unverified or any pair is unresolved. `scribeforge.adapters.epub` renders that book as a deterministic EPUB 3 archive (stored `mimetype`, container, package, nav, one XHTML per page) with XML-escaped text, and `write_epub` replaces the destination atomically. `scribeforge.adapters.docx` writes the same book as a deterministic WordprocessingML package (`[Content_Types].xml`, package relationships, `word/document.xml`, `docProps/core.xml`), one `<w:p>` per reconstructed paragraph (headings styled, footnotes superscript) and an explicit page break between pages; both adapters share fixed zip timestamps, atomic replacement, and metadata validation through `scribeforge.adapters.export_common`. `scribeforge export --store <dir> --out <file> [--language <tag>]` selects the format from the extension and exits `2` rather than exporting a partial book. Export metadata is overridable: `--title`, `--author` (written as `dc:creator` in both formats), and `--modified` (ISO 8601, naive values treated as UTC) for byte-reproducible builds. Both exporters reject characters XML 1.0 cannot represent, naming the offending page and line, instead of writing an archive no reader can open. Each build is then re-read by `scribeforge.adapters.export_check`, which verifies archive structure, manifest/relationship targets, spine order, and that every page reproduces its accepted blocks exactly (whitespace squashed, characters not). See [Paragraphs and structural fidelity](./paragraphs.md). The Flutter desktop workspace now exposes this verified export path with a metadata dialog for EPUB 3/DOCX, language, title, author, and an optional fixed ISO 8601 modification timestamp. It calls the same CLI/export validators as command-line use, refuses a source-identity mismatch, and does not confuse unverified A/B candidate exports with the accepted final text. Disputed pairs are now re-read rather than only queued: `scribeforge repair`
(and `process --repair-rounds N`) cuts each disputed line out of the page again,
wider and enlarged, re-reads it with every configured reader, re-asks the vision
model on the same crop, and accepts a text only when at least two independent
readers agree — stopping on budget, on exhausted attempts, or when nothing is
left to try. `progress()` reports the automatic-acceptance rate so an operator can
choose between spot-checking and another round; the limits (missed lines, common
misreads) are stated in [Self-comparison and self-correction](./self-correction.md).
Remaining work includes pipeline-level benchmark variants C/D, a real corpus report, and richer faithful source EPUB layout rendering.

## Phase 5 — ancient books and old-edition scans (current top priority)

A 2026-10-28 review against `benchmark/V..pdf` (562 pages) plus a full source audit
established that the pipeline handles clean modern print and book-scale
processing but not complex-layout, non-standard, low-quality scans of ancient
books and old editions. Old-book/ancient-book recognition with faithful EPUB and
DOCX output is now the primary development goal.

Four P0 gaps block that claim:

1. **No image preprocessing layer.** PDF pages are rasterized and handed straight
   to OCR engines: no deskew, deshadow, binarization, denoise or resolution
   adaptation. Old-book scans are routinely skewed, shadowed and faded.
2. **No vertical / right-to-left support.** Alignment matches on Y-axis centre
   distance, which is semantically wrong for vertical columns — the mainstream
   layout of ancient Chinese books.
3. **Layout analysis is delegated entirely to MinerU's `basic` tier.** The worker
   flattens each MinerU block into a single "line" with an empty token list, so
   multi-column pages, mixed text/figure pages, marginalia, character-level
   confidence and precise review crops are all unavailable.
4. **No chapter or printed-page model.** `pages` has only a physical index;
   EPUB navigates "Page 1…Page N"; documents whose sections restart page
   numbering, and blank/cover/front-matter pages, cannot be represented.

Two defects found by the same review were fixed with regression tests:

- `normalize_polygon` and `mineru_worker._box` produced out-of-range geometry for
  block polygons that cross the page edge or carry negative coordinates after
  deskew, which made `BoundingBox` reject the whole page. Both now clamp.
- `align_pages` computed text similarity for every line pair before applying the
  cheap vertical-distance filter. Moving the geometric rejection first made
  dense-page alignment up to 27× faster (80 lines: 1018 ms → 37 ms) with
  identical matching results.

Status as of 2026-10-29: the preprocessing layer (#85), worker contract v2
(#86), chapter/printed-page/role model (#87), glyph-variant comparison layer
(#88) and page-furniture removal (#89) are implemented and covered by tests; the
engine route (#90) is documented in [Technology route](./technology-route.md),
with PaddleOCR-VL and Surya adoption still to be executed (stages R1–R2).

What remains genuinely open is vertical text: `align_pages` still pairs lines on
Y-axis centre distance, which is semantically wrong for vertical columns, and no
vertical/ traditional-character benchmark corpus exists yet. Until a real vertical
scan is measured, the README gate stands: ancient-book support must not be claimed.

Staged plan and gates: see [Ancient book support](./ancient-book-support.md) §8.
The P0 gate — preprocessing, worker contract v2, vertical text, and
chapter/page model, then a measurable run on a real traditional-character
vertical ancient-book scan — must pass before user-facing material claims
ancient-book support.

## Later
Speech-to-text and text-to-speech may join the same text library only after book digitization is reliable.
