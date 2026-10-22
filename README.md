# ScribeForge

**Local-first, evidence-grade digitization for scanned literature.**

ScribeForge turns scanned books into high-confidence, traceable digital text and exports verified results to EPUB/DOCX.

The project is not trying to build yet another OCR engine. It orchestrates multiple OCR/document-analysis engines, detects disagreements, preserves page-level evidence, escalates only uncertain regions to a vision-language model or human review, and keeps every final decision traceable to the source scan.

## 新手快速开始（中文）

如果你是第一次拉取仓库，建议先阅读 **[中文新手使用指南](./docs/user-guide-zh.md)**。指南包含 Windows 11 下的 Python/Flutter 安装、首次 OCR 运行环境配置、扫描 PDF 处理、候选 EPUB 生成、Flutter 桌面比较、评估文件位置与常见故障排查。

最短流程（已安装 Python 3.11 与 Flutter 3.47.7 的 Windows 开发环境）：

```powershell
git clone https://github.com/BrainEno/ScribeForge.git
cd ScribeForge
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
scribeforge setup
scribeforge process "D:\Books\My Book.pdf"
scribeforge export-candidates --store "D:\Books\My Book.scribeforge" --out-dir "D:\Books\My Book-comparison-candidates"
cd clients\flutter_desktop
.\tool\bootstrap_desktop.ps1
flutter run -d windows
```

也可以在 Flutter 工作区中使用 **Set up OCR / Run OCR / Build A/B EPUBs** 按钮。首次使用请点击右上角设置图标确认 ScribeForge CLI 的路径。比较器只辅助人工判断，不会自动认定哪份候选 EPUB 更好。

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
clients/flutter_desktop/ Flutter desktop PDF + EPUB comparison workspace
.github/workflows/     Python + Flutter CI enforcement
```

## Development status

The repository is in **Phase 3 — desktop UI**, with Phases 1, 2, and 4 complete. Phase 0 (benchmark harness), Phase 0.5 (private runtime manager plus a first-run setup wizard), the low-level verification path, the resumable CLI slices (PDF/image import, page-granular orchestration, runtime-backed workers, `scribeforge process`/`status`), the decision layer (deterministic adjudication plus the human review queue), and EPUB/DOCX export with integrity self-checks are implemented and covered by tests.

## CLI

```bash
scribeforge setup                             # first-run runtime/model setup wizard
scribeforge setup --runtime-root runtime/     # choose a private runtime directory
scribeforge process book.pdf                  # import + process every page
scribeforge process book.pdf --store store/   # explicit project store
scribeforge status --store store/             # persisted jobs + review queue
scribeforge audit --store store/ --json        # read-only project integrity audit
scribeforge backup --store store/ --out Book.zip # project DB, assets and verified source PDF
scribeforge verify-backup --backup Book.zip     # verify hashes, manifest and SQLite integrity
scribeforge restore --backup Book.zip --to Book-restored.scribeforge # restore to new folder
scribeforge review --store store/             # pairs awaiting a human decision
scribeforge resolve --store store/ \
  --page 0 --pair 3 --text "鸦"              # record the accepted reading
scribeforge export --store store/ --out book.epub   # verified text -> EPUB 3
scribeforge export --store store/ --out book.docx   # verified text -> DOCX
scribeforge export-candidates --store store/ --out-dir comparison/ # raw OCR EPUB candidates
cd clients/flutter_desktop && flutter run -d windows # Flutter comparison UI
scribeforge export --store store/ --out book.epub \
  --title "书名" --author "作者" --modified 2026-10-20T00:00:00Z
scribeforge ui --store store/                 # keyboard-first desktop review shell
scribeforge benchmark --manifest benchmark/manifest.json \
  --out benchmark/benchmark_report.md         # score MinerU + PaddleOCR
```

`process` writes a project store beside the book by default (`<book-stem>.scribeforge`). OCR runtimes are shared in the per-user application data folder by default; `setup`, `process`, and `benchmark` accept `--runtime-root` to override it. Run `scribeforge setup` once to install the private Python environments and OCR models. Exit codes are semantic: `0` every page processed, `1` some pages failed (retryable), `2` fatal setup error (missing file, unsupported source, backend runtime not installed).

Run `scribeforge audit --store store/ --json` before archiving or resuming a project to check SQLite integrity, foreign-key references, source-file SHA-256, and whether every imported page image still exists. The audit opens SQLite read-only and never repairs or changes evidence. Exit code 0 means no findings, 1 means audit findings need attention, and 2 means the audit could not read the project database.

Create a project backup with `scribeforge backup --store store/ --out Book.zip`. The archive includes a consistent SQLite snapshot, all regular project-store files, and each original source document after confirming its recorded SHA-256. Backup is refused while a job is running or when database/page evidence is damaged or the source identity cannot be verified. Existing archives are never overwritten unless `--overwrite` is supplied. Run `scribeforge verify-backup --backup Book.zip` before restoring; `scribeforge restore --backup Book.zip --to Book-restored.scribeforge` verifies member hashes and SQLite integrity, then restores into a new directory without overwriting an existing destination. Internal page-image and source paths are updated to the restored location; processing/review evidence remains intact.

When two engines agree a decision is recorded as `automatic`; a confident VLM reading is recorded as `vlm`. Anything else stays in the review queue for a human decision — ScribeForge never rewrites source OCR. `review` prints each queued pair with both engine readings, the VLM reading (when one exists), its risk score/reasons, and the crop file to inspect; `resolve` appends a `human` decision that cites the verification run, both engine runs, and any VLM reading, superseding earlier automatic/VLM decisions on that pair without deleting them.

`export-candidates` writes explicitly **unverified** raw OCR outputs for each requested engine (MinerU and PaddleOCR by default), preserving the source page index in each XHTML spine item. It also writes a `candidate-manifest.json` with source hash, engine version, run IDs, candidate hashes and paths. These files are for visual comparison only and are never accepted by the verified export pipeline. `export` writes the accepted decisions as an EPUB 3 or DOCX file (chosen by the destination extension) and refuses to run while any page is unverified or any pair is still unresolved, so a partial book can never be exported silently. `--title` and `--author` override the export metadata (the author becomes `dc:creator` in both formats), and `--modified` pins the timestamp for byte-reproducible builds. Every build is re-read and checked against the accepted text before it is written. See [Export](./docs/export.md).

`ui` opens the desktop review shell over the store: library/job status, an unresolved-pair queue, evidence, page raster, and review crop. Selecting a conflict focuses its region on the source-page preview; `a`/`d` locate the primary/secondary OCR line, while `p` restores the pair region. The `Compare` tab displays both OCR lines side by side and highlights persisted character-level substitutions, insertions, deletions, and punctuation differences. Reviewers can explicitly accept either OCR reading or a VLM reading, or enter literal corrected text; keyboard shortcuts (`1`, `2`, `3`, `n`, `Ctrl+Enter`) keep the common path fast. Source highlights are drawn only on transient previews, and each decision appends a human record with evidence references. See [Desktop shell](./docs/ui.md).

`clients/flutter_desktop` is the Flutter-first desktop comparison surface: the original PDF is shown beside two EPUB candidates, changed lines are highlighted, and reviewers can save per-page preferences, an overall winner, and notes. Prepare candidate files using `scribeforge export-candidates`, then follow [Flutter Desktop](./clients/flutter_desktop/README.md) to generate platform runners and launch it. The first version compares extracted EPUB text rather than reproducing all EPUB CSS/layout, and chapter-based EPUBs are aligned approximately. On Windows, enable Developer Mode before compiling `pdfrx`, which uses native asset symlinks. Human review choices are stored beside the PDF with input hashes; the UI never decides a winner automatically.

`benchmark` validates a corpus manifest (every case needs a non-empty page image and hand-verified ground truth), builds only the requested managed backends, and scores each one for CER, WER, punctuation, missing/extra lines, per-page time, and pages/minute. It writes a deterministic Markdown report naming the manifest SHA-256, with per-case rows so an aggregate gain cannot hide a hard-page regression. Variants A (MinerU) and B (PaddleOCR) are runnable; the pipeline comparison (C/D) and the real-corpus report remain. See [Benchmark](./benchmark/README.md).

See:

- [AGENTS.md](./AGENTS.md)
- [Architecture](./docs/architecture.md)
- [Development plan](./docs/development-plan.md)
- [TDD policy](./docs/tdd.md)
- [Data model](./docs/data-model.md)
- [Export](./docs/export.md)
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
