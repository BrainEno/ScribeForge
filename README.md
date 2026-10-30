# ScribeForge

**Local-first, evidence-grade digitization for scanned literature.**

ScribeForge turns scanned books into high-confidence, traceable digital text and exports verified results to EPUB/DOCX.

The project is not trying to build yet another OCR engine. It orchestrates multiple OCR/document-analysis engines, detects disagreements, preserves page-level evidence, escalates only uncertain regions to a vision-language model or human review, and keeps every final decision traceable to the source scan.

## 新手快速开始（中文）

如果你是第一次拉取仓库，建议先阅读 **[中文新手使用指南](./docs/user-guide-zh.md)**。指南涵盖运行环境、扫描 PDF 导入、页级处理进度、人工复核、EPUB/DOCX 导出及常见故障排查。

源码方式启动 Flutter 一体化工作台（Windows；需要 Python 3.11 和 Flutter 3.47.7 stable）：

```powershell
git clone https://github.com/BrainEno/ScribeForge.git
cd ScribeForge
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
cd clients\flutter_desktop
.\tool\bootstrap_desktop.ps1
flutter run -d windows
```

启动后使用 **书籍 → 导入书籍 → 处理 → 复核 → 候选比较（可选）→ 导出成品** 完成主要流程；候选比较只记录原始 OCR EPUB 的人工质量评价，不会接受文本或影响可信导出。**设置** 页面可检查 OCR 运行环境并打开现有配置向导。应用通过本地 CLI 与 Python 核心通信，不会自动运行 VLM。当前仓库提供源码开发启动方式，尚未发布预编译桌面安装包；运行工作台需要 Flutter SDK。

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

### Current top priority: ancient books and old-edition scans

A 2026-10-28 review of the pipeline against a 562-page real scan found that
ScribeForge handles **clean modern horizontal print** and book-scale processing
(500–800 pages, resumable, page-granular), but does **not** yet handle
**complex-layout, non-standard, low-quality scans of ancient books and old
editions**. Four P0 gaps were identified: no image preprocessing, no
vertical/right-to-left text support, layout analysis delegated entirely to
MinerU's basic tier, and no chapter/printed-page model.

Handling old books and ancient books — and exporting them faithfully to EPUB and
DOCX — is therefore the **primary development goal** until the P0 gate is met.

Since that review, five of the six follow-up issues have shipped: the
preprocessing stage (deskew, deshadow, contrast, page-adaptive restoration), the
OCR worker contract v2 (block types, line-level geometry, confidence, direction),
the chapter/printed-page/page-role model with chapter-based EPUB navigation,
glyph-variant folding for comparison, page-furniture removal, and vertical /
right-to-left alignment (the pairing axis and reading order now follow the page
direction instead of assuming horizontal rows). What is still missing before the
gate can be declared passed is a real vertical / traditional-character benchmark
corpus and measured end-to-end CER: every accuracy number in this repository is
still either pixel-level or unmeasured. The engine route is in [Technology route](./docs/technology-route.md).
The full assessment, prioritised gap list, verified free/open-source technology
recommendations and the staged plan live in
[Ancient book support](./docs/ancient-book-support.md). Do not claim
ancient-book support in user-facing material before that gate is passed.

Export used to write one `<p>` per accepted text line, so a scanned page arrived
as 30-odd hard-wrapped fragments — 161 paragraphs for the 36 paragraphs that the
six `benchmark/corpus/v` pages actually contain. `VerifiedPage.blocks()` now
reconstructs paragraphs from line geometry and engine block labels (indent, short
line, line gap, sentence-final punctuation, hyphenless-Latin rules), keeps
headings and footnotes distinct (`<h2>`, `<aside epub:type="footnote">`), and the
six pages recover **36 / 36** paragraphs exactly, with no character lost. The
evidence layer is untouched: `VerifiedPage.lines` stays one entry per accepted
line and every export is re-read and compared block by block.

Export also stopped writing one XHTML file per scan. A verified book now re-flows
into **one document per chapter** (`--layout chapter`, the default once a chapter
tree exists), so a paragraph cut by a page turn is one paragraph again: two
independent signals must agree before lines are joined — the previous page's last
line does not end a sentence and is not a short line, and the next page's first
line is not indented. Every scanned page still leaves an
`epub:type="pagebreak"` marker carrying its printed folio, and the self-check
proves each page appears exactly once, in order, before the file is written.
On the six real transcriptions in `benchmark/corpus/v`, placing the page break
inside the page's longest paragraph rejoins the paragraph on **6 / 6** pages with
the paragraph count and every character unchanged (`pytest tests/test_flow.py -m
benchmark`). `--layout page` keeps the old facsimile shape for comparing against
the scans. See [Export](./docs/export.md) and
[Paragraphs and structural fidelity](./docs/paragraphs.md).

Export also carries what reflow cannot. A scanned page with **no accepted text**
becomes an image, so the book's **cover** (declared `cover-image` plus the EPUB 2
`meta name="cover"`) and its confirmed **plates** (`<figure>` at their own page
break) reach the reader instead of disappearing; a page of prose is still re-flowed,
never mirrored. The raster is copied byte for byte and its SHA-256 is re-derived
from the written archive, so a swapped page image fails the build. On this book the
cover measures **94.05% ink** and no engine reads a line from it, which is why the
rule is ink *plus* position (a picture on the first page of the scan is the cover)
rather than text. DOCX stays text-only and prints what it does not carry.

A disagreement no longer ends in the human queue by default. `scribeforge repair`
re-reads each disputed line from a wider, enlarged crop, stores every re-read as
region-scoped evidence, and accepts a text only when **two independent readers**
agree — the two whole-page engines, the fresh re-reads, and (when configured) a
vision model re-reading the same crop. Rounds stop on budget, when attempts run
out, or when nothing is left to try, and `src/scribeforge/pipeline/rounds.py`'s
`progress()` reports how much of the book was accepted without a person, so the
operator can decide between spot-checking and another round. A vision model's
self-reported confidence is no longer enough on its own: it must corroborate an
engine (issue #76). What this cannot do is see a line that *both* engines missed,
or a glyph both misread the same way — that needs an independent full-page reader
and is listed as the next step. See
[Self-comparison and self-correction](./docs/self-correction.md).

## Planned processing pipeline

```text
Import
  -> page decoding
  -> preprocessing (deskew / deshadow / restoration, page-adaptive)
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
clients/flutter_desktop/ Flutter desktop end-to-end OCR workflow
.github/workflows/     Python + Flutter CI enforcement
```

## Development status

The repository is in **Phase 3 — desktop workflow integration**, with the Python processing, decision, and export layers implemented. The Flutter client now provides a guided single-book flow for import/open, source identity checks, runtime inspection and setup, resumable processing with page/stage progress, evidence-backed keyboard-assisted human review, separate A/B evaluation of unverified OCR EPUBs, EPUB 3/DOCX export, and a project-safety center for read-only audit, backup, archive verification, and non-overwriting restore. Flutter invokes the local Python CLI through versioned JSON contracts; candidate ratings remain in a source/candidate-hash-bound sidecar and do not affect accepted text or verified export. The shared Flutter visual standards and component rules are in [`design-pattern.md`](./design-pattern.md). The desktop client is currently source-run only; prebuilt installers and a bundled Python/OCR runtime are not yet provided.

**Phase 5 — ancient books and old-edition scans — is now the top development priority**
(see the section above and [Ancient book support](./docs/ancient-book-support.md)).
Its accuracy report is still pending: the review environment has no GPU and no access
to model hosting, so no real A/B/C/D accuracy run has been possible. What does
exist are reproducible pixel-level measurements (preprocessing separability and
residual skew on real corpus pages) — those are not a substitute for CER.

## CLI

```bash
scribeforge setup                             # first-run runtime/model setup wizard
scribeforge runtime-status --json                 # inspect backend readiness and hardware
scribeforge vlm-check --image crop.png --vlm-endpoint http://127.0.0.1:1234/v1 --vlm-model local-vision-model --json # test a VLM endpoint; no project data is changed
scribeforge setup --runtime-root runtime/     # choose a private runtime directory
scribeforge process book.pdf                  # import + process every page
scribeforge process book.pdf --vlm-endpoint http://127.0.0.1:1234/v1 --vlm-model local-vision-model # optional local crop review
scribeforge process book.pdf --store store/   # explicit project store
scribeforge status --store store/             # persisted jobs + review queue
scribeforge structure --store store/ --json   # page roles, printed page numbers, chapter tree
scribeforge audit --store store/ --json        # read-only project integrity audit
scribeforge backup --store store/ --out Book.zip # project DB, assets and verified source PDF
scribeforge verify-backup --backup Book.zip     # verify hashes, manifest and SQLite integrity
scribeforge restore --backup Book.zip --to Book-restored.scribeforge # restore to new folder
scribeforge review --store store/             # pairs awaiting a human decision
scribeforge resolve --store store/ \
  --page 0 --pair 3 --text "鸦"              # record the accepted reading
scribeforge confirm --store store/ --page 5 \
  --kind blank --reason "empty leaf"        # declare a page has no text
scribeforge export --store store/ --out book.epub   # verified text -> EPUB 3
scribeforge export --store store/ --out book.docx   # verified text -> DOCX
scribeforge export --store store/ --out book.epub --json           # acceptance report
scribeforge export --store store/ --out book.epub --strict-acceptance # machine-certified only
scribeforge export --store store/ --out scans.epub --layout page    # facsimile layout
scribeforge export-candidates --store store/ --out-dir comparison/ # raw OCR EPUB candidates
cd clients/flutter_desktop && flutter run -d windows # Flutter end-to-end workflow
scribeforge export --store store/ --out book.epub \
  --title "书名" --author "作者" --modified 2026-10-20T00:00:00Z
scribeforge ui --store store/                 # keyboard-first desktop review shell
scribeforge benchmark --manifest benchmark/manifest.json \
  --out benchmark/benchmark_report.md         # score MinerU + PaddleOCR
```

`process` writes a project store beside the book by default (`<book-stem>.scribeforge`). OCR runtimes are shared in the per-user application data folder by default; `setup`, `process`, and `benchmark` accept `--runtime-root` to override it. Run `scribeforge setup` once to install the private Python environments and OCR models. Use `scribeforge runtime-status --json` to inspect hardware, backend readiness, and private model/cache paths without changing the installation. Exit codes are semantic: `0` every page processed, `1` some pages failed (retryable), `2` fatal setup error (missing file, unsupported source, backend runtime not installed).

Run `scribeforge audit --store store/ --json` before archiving or resuming a project to check SQLite integrity, foreign-key references, source-file SHA-256, and whether every imported page image still exists. The audit opens SQLite read-only and never repairs or changes evidence. Exit code 0 means no findings, 1 means audit findings need attention, and 2 means the audit could not read the project database.

Create a project backup with `scribeforge backup --store store/ --out Book.zip`. The archive includes a consistent SQLite snapshot, all regular project-store files, and each original source document after confirming its recorded SHA-256. Backup is refused while a job is running or when database/page evidence is damaged or the source identity cannot be verified. Existing archives are never overwritten unless `--overwrite` is supplied. Run `scribeforge verify-backup --backup Book.zip` before restoring; `scribeforge restore --backup Book.zip --to Book-restored.scribeforge` verifies member hashes and SQLite integrity, then restores into a new directory without overwriting an existing destination. Internal page-image and source paths are updated to the restored location; processing/review evidence remains intact.

When two engines agree a decision is recorded as `automatic`; a confident VLM reading is recorded as `vlm`. Anything else stays in the review queue for a human decision — ScribeForge never rewrites source OCR. `review` prints each queued pair with both engine readings, the VLM reading (when one exists), its risk score/reasons, and the crop file to inspect; `resolve` appends a `human` decision that cites the verification run, both engine runs, and any VLM reading, superseding earlier automatic/VLM decisions on that pair without deleting them.

A scanned book is not a list of rasters. `structure` derives what each page *is*
(cover, copyright, contents, front matter, body, plate, blank), the printed page
number a reader would cite (three separate numbering runs in this book, restarting
at 1), and the chapter tree — from the book's own contents page when an entry can be
proved against the page it points at, otherwise from heading typography. The EPUB
nav then mirrors that tree instead of emitting 562 flat `Page N` entries, each
chapter becomes a real document instead of a nav entry pointing at a flat run of
pages, and every page keeps its printed folio in a page-break marker. Roles are decided from engine report plus
a **measured ink ratio** (this book: blank leaves 0.39%, sparsest text page 2.45%,
body 6-13%, coloured cover 94%): a textless page measuring under 1% is confirmed
blank by the measurement, while anything above it stays blocked until a person says
whether it is a plate or a page the engines failed to read. See
[Book structure](./docs/book-structure.md).

Two facts are never inferred. First, *agreement is not acceptance*: `adjudicate_pair` sends every automatic path through an acceptance gate that refuses text no machine should certify — the replacement character `U+FFFD`, XML-illegal control characters, a pair where both readers admit low confidence, or a pair whose evidence-quality risk exceeds the policy ceiling. Blocked pairs stay in `review_queue`, which lists every undecided pair (not just score-thresholded candidates), so a blocked pair always has a command that can clear it. Second, *an empty page is not a blank page*: a page with zero aligned pairs blocks export until `scribeforge confirm` records who says it carries no text and why. The export report says how much of the book a machine certified (`machine_accepted` vs `human_accepted`, the highest machine-accepted content risk, and the pairs worth a spot check); `--strict-acceptance` refuses while any of those remain. See [The acceptance gate](./docs/acceptance-gate.md).

`export-candidates` writes explicitly **unverified** raw OCR outputs for each requested engine (MinerU and PaddleOCR by default), preserving the source page index in each XHTML spine item. It also writes a `candidate-manifest.json` with source hash, engine version, run IDs, candidate hashes and paths. These files are for visual comparison only and are never accepted by the verified export pipeline. `export` writes the accepted decisions as an EPUB 3 or DOCX file (chosen by the destination extension), re-flowing EPUB into chapter documents with page-break markers (`--layout chapter`, the default when a chapter tree exists; `--layout page` for the facsimile shape; DOCX stays page-by-page), and refuses to run while any page is unverified or any pair is still unresolved, so a partial book can never be exported silently. `--title` and `--author` override the export metadata (the author becomes `dc:creator` in both formats), and `--modified` pins the timestamp for byte-reproducible builds. Every build is re-read and checked against the accepted text before it is written. See [Export](./docs/export.md).

`ui` opens the desktop review shell over the store: library/job status, an unresolved-pair queue, evidence, page raster, and review crop. Selecting a conflict focuses its region on the source-page preview; `a`/`d` locate the primary/secondary OCR line, while `p` restores the pair region. The `Compare` tab displays both OCR lines side by side and highlights persisted character-level substitutions, insertions, deletions, and punctuation differences. Reviewers can explicitly accept either OCR reading or a VLM reading, or enter literal corrected text; keyboard shortcuts (`1`, `2`, `3`, `n`, `Ctrl+Enter`) keep the common path fast. Source highlights are drawn only on transient previews, and each decision appends a human record with evidence references. See [Desktop shell](./docs/ui.md).

`clients/flutter_desktop` is the Flutter-first, single-book pipeline workspace: import or resume a project, check/relink its source identity, inspect runtime readiness, run OCR with page/stage progress, review OCR/VLM evidence, record explicit human decisions, compare unverified candidate EPUBs, export accepted EPUB 3 or DOCX, and audit/backup/verify/restore projects from **项目安全**. Candidate-fill shortcuts are Alt+1/2/3; Ctrl+Enter records the edited decision. The client communicates with Python through versioned JSON CLI contracts; candidate A/B ratings remain separate from accepted decisions, restore requires explicit confirmation and a new directory, and the client does not silently launch a VLM or auto-accept its suggestions. See the [Flutter UI design pattern](./design-pattern.md) and [Flutter Desktop setup](./clients/flutter_desktop/README.md). A Flutter SDK is required today; prebuilt desktop installers are not yet published.

`benchmark` validates a corpus manifest (every case needs a non-empty page image and hand-verified ground truth), builds only the requested managed backends, and scores each one for CER, WER, punctuation, missing/extra lines, per-page time, and pages/minute. Add --include-pipeline to also score variant C through production dual-engine alignment/risk/decision stages; unresolved pairs are omitted rather than guessed and reported as coverage counts. Add --include-vlm --vlm-endpoint http://127.0.0.1:1234/v1 --vlm-model local-vision-model to compare variant D. Only crop images are sent; remote endpoints require HTTPS and explicit --allow-remote-vlm. It writes a deterministic Markdown report naming the manifest SHA-256, with per-case rows so an aggregate gain cannot hide a hard-page regression. Variants A (MinerU), B (PaddleOCR), C (conservative dual-engine decisions), and D (VLM-assisted decisions through an explicit OpenAI-compatible endpoint) are runnable. ScribeForge does not install or silently start a VLM server; the real-corpus report remains pending. See [Benchmark](./benchmark/README.md).

See:

- [AGENTS.md](./AGENTS.md)
- [Ancient book support (current top priority)](./docs/ancient-book-support.md)
- [Architecture](./docs/architecture.md)
- [Development plan](./docs/development-plan.md)
- [TDD policy](./docs/tdd.md)
- [Data model](./docs/data-model.md)
- [Export](./docs/export.md)
- [Paragraphs and structural fidelity](./docs/paragraphs.md)
- [Self-comparison and self-correction](./docs/self-correction.md)
- [Desktop shell](./docs/ui.md)
- [Flutter Desktop comparison client](./clients/flutter_desktop/README.md)

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
