# ScribeForge Flutter Desktop

The Flutter desktop client is the guided, single-book workflow for ScribeForge: import/open a project, run its resumable OCR pipeline, inspect the evidence-backed review queue, compare raw OCR candidate EPUBs, export an accepted EPUB 3 or DOCX, and manage project safety without leaving the app. The **项目安全** page runs a read-only audit, creates backups, verifies archives, and restores only to a new directory after explicit confirmation. Python owns OCR, persistence, and export; Flutter invokes the local `scribeforge` executable with argument lists and reads its versioned JSON contracts.

The shared visual language and reusable widget rules live in the repository-root [`design-pattern.md`](../../design-pattern.md). New pages should use its theme tokens, workspace components, operation states, and accessibility checklist.

## Requirements

### End-user desktop package

A complete package contains `backend/scribeforge(.exe)` beside the Flutter application (or under `Contents/Resources/backend` on macOS). This frozen backend includes the CLI and native PDF dependencies, so users do **not** install Python or add a Python environment to PATH. On first launch, **设置 → 初始化环境** prepares uv, private Python 3.12 environments, MinerU, PaddleOCR, and their models. That step still requires network access and additional disk space; model weights and a VLM server are not bundled.

### Development

- Flutter 3.47.7 stable (Dart 3.13+).
- Windows, macOS, or Linux desktop development support.
- For source-only development without a frozen side-by-side backend, no manual venv/pip commands are needed: open **设置 → 后端环境（Python 虚拟环境）** and click **自动创建后端环境**. The client auto-detects the system Python (trying the `py -3` launcher, `python3`, `python` and common install locations; if none is found it shows Chinese install guidance instead of a raw error), lets developers override the interpreter path and repository root manually, creates `backend-venv` under the settings directory, runs `pip install -e <repo root>` inside it, smoke-checks `scribeforge --help`, then automatically runs `setup` to download the OCR models, and finally points **命令行工具** at the resulting executable automatically. The whole flow is one click with Chinese progress dialogs; every step remains overridable for developers.

  The explicit **设置 → 命令行工具** override remains available for this mode. Without an override or bundle, PATH is a development fallback and is identified as such in Settings.

- OCR backends installed through **Settings → 初始化环境**. The Flutter-native guide streams per-backend/per-step progress and can repair either backend independently. Processing is blocked with a setup action while the core or required backends are unavailable, rather than starting OCR and surfacing a raw missing-module exception.

## First-time setup and launch

From this directory:

```bash
flutter create --platforms=windows,macos,linux --project-name scribeforge_desktop .
flutter pub get
```

Windows PowerShell users can run `./tool/bootstrap_desktop.ps1`; macOS/Linux users can run `sh tool/bootstrap_desktop.sh`. The generated `windows/`, `macos/`, and `linux/` folders are local platform runners. CI scaffolds them from Flutter templates before analyzing and testing.

Both bootstrap scripts pin dependency resolution to the official `https://pub.dev` / `https://storage.googleapis.com` (unless you export `PUB_HOSTED_URL`/`FLUTTER_STORAGE_BASE_URL` yourself). Third-party pub mirrors such as `pub.flutter-io.cn` re-pack some archives (`pdfrx`, `pdfrx_engine`), which makes `flutter pub get` / `flutter run` abort with `Downloaded archive ... had wrong content-hash`. If you run `flutter run` directly and hit that error, either use the bootstrap script once or point your environment at the official endpoints:

```powershell
# PowerShell
$env:PUB_HOSTED_URL = "https://pub.dev"
$env:FLUTTER_STORAGE_BASE_URL = "https://storage.googleapis.com"
```

```bash
# macOS/Linux
export PUB_HOSTED_URL=https://pub.dev
export FLUTTER_STORAGE_BASE_URL=https://storage.googleapis.com
```

Run the desktop app:

```bash
flutter run -d windows
# or: flutter run -d macos
# or: flutter run -d linux
```

If the CLI is not on `PATH`, either use **设置 → 后端环境（Python 虚拟环境） → 自动创建后端环境** (recommended; no commands to type) or open **Settings → 命令行工具** and enter the full path to the executable in the Python environment where ScribeForge is installed.

## Guided workflow

The app bar's help button (使用帮助) opens a dialog listing the standard
workflow (导入书籍 → 开始处理 → 复核 → 导出), all review shortcuts
(Alt+←/→, Ctrl+J/G, Alt+1/2/3, Ctrl+Enter) and the rule that filling a
candidate is not a recorded decision.

A persistent **下一步** banner under the notice bar always shows the single
next action implied by the current state — configure the CLI (设置), one-click
初始化环境 on first run, 导入书籍, 重新关联源文件, 去处理, 去复核, or 去导出 —
with a button that jumps straight to the right page. The suggestion is computed
by a pure function (`nextStepGuidance`) with exhaustive unit tests. When a
project was opened earlier, its store directory is remembered in the settings
(`last_project_store`) and the banner offers **继续上次项目** on a cold start,
reopening it through the same validation as 打开项目文件夹.

1. **书籍** — Choose **导入书籍** for a PDF or page image. The default store is a sibling directory named `<source-stem>.scribeforge`. Choose **打开项目文件夹** to resume an existing single-book store.
2. **处理** — Run `scribeforge process --cancelable`. The UI polls `status --json` while the command is active, showing pages with both OCR results, the active page/stage, job counts, and failed-page details. **取消处理** sends a versioned control message to the CLI; it stops the OCR worker and its descendants, marks an interrupted stage retryable, and preserves imported page images, completed OCR evidence, and human decisions. Worker deadlines default to 600 seconds per OCR invocation and can be configured with `--worker-timeout SECONDS`; worker stdout/stderr limits prevent unbounded log capture. Processing is resumable; rerunning reuses successful page evidence and retries incomplete/failed work. VLM review is off by default; only enabling it in **设置 → 视觉模型复核** passes the configured endpoint/model to the CLI.
3. **复核** — Read both OCR candidates, any VLM reading and its uncertainty flag, risk reasons, and the crop (or original page image). Previous/next controls, **Alt+←/→**, and a one-based page jump move through the queue. **Ctrl+J** focuses the decision editor and **Ctrl+G** focuses page jump. Candidate buttons and **Alt+1/2/3** only fill text; **Ctrl+Enter** or **记录人工决策** explicitly calls `resolve`, appends a decision, and never overwrites source OCR.
4. **候选比较** — After every page has both OCR results, choose **生成 OCR 候选 EPUB** to call `export-candidates`, or load two EPUBs manually. Compare aligned pages and line differences, jump to the next differing page, and explicitly rate each page **A/B/Tie/稍后再评**. These quality ratings and whole-book notes are saved to `<source>.scribeforge-review.json`, bound to source/candidate SHA-256 values. They do not accept text, alter the trusted review queue, or unblock final export. A failed generation can be retried.
5. **导出** — Select EPUB 3 or DOCX, choose an output folder (the filename follows the book title and format), and set language plus optional author. Strict acceptance blocks export when unresolved content remains. Candidate OCR output is not an accepted final export.
6. **项目安全** — Run the read-only audit and inspect database/page-evidence findings; choose a backup directory to create a non-overwriting ZIP; select a ZIP to verify its hashes and SQLite integrity; then choose a parent folder and explicitly confirm restore to a generated new directory. The UI binds restore eligibility to the verified archive fingerprint, surfaces CLI errors for retry, and loads a successful restore into the workbench. Existing project directories are never overwritten.
7. **设置** — Inspect runtime and hardware status, launch the first-run setup wizard, refresh status, configure the CLI executable, provision the backend Python environment automatically (**后端环境 → 自动创建后端环境**, no manual venv/pip commands), or explicitly opt in to VLM review. The VLM switch starts off; endpoint/API-key environment variable/timeout settings are saved locally, never secret values. Remote HTTPS requires a separate opt-in.

Each project store is expected to contain one book. Before processing, the client checks the selected source path and SHA-256 against the project record. If a source has moved, **重新关联源文件** calls `relink-source`; it updates only the path after the source bytes match. A modified source is not silently applied to old jobs or OCR evidence.

The setup action is a native Flutter dialog rather than the legacy Tk window. It displays backend state, the current phase and step count, final failure details, and a one-backend repair action. Status inspection is read-only; **刷新运行时状态** re-reads the CLI report. Direct `scribeforge setup` without `--json` remains available as the CLI's Tk-based fallback.

VLM review is strictly opt-in. With the switch off, the desktop process command contains no VLM endpoint/model options, so review-crop images are not sent to a model. When explicitly enabled, the CLI stores the literal model output, uncertainty flag, crop path and SHA-256, and cites that immutable review record from any resulting machine adjudication. The review UI presents VLM text as evidence only; filling a candidate or navigating never records a human decision. Each human decision still requires the explicit **记录人工决策** action or its documented **Ctrl+Enter** shortcut.

A signed public Windows release is not yet published. The checked-in Windows packaging workflow can produce an unsigned portable ZIP and per-user installer, with frozen-backend smoke checks and checksums. Packaging and remaining fresh-VM release gates are documented in [`docs/windows-desktop-distribution.md`](../../docs/windows-desktop-distribution.md); running from source still requires Flutter and either the Python CLI or a separately built frozen backend.

## Python CLI contract used by Flutter

The client communicates through local child processes, not a network service. It never parses human-readable `review` output for queue entries.

- `scribeforge status --store <store> --json` emits `schema_version: 1`. Each project includes total pages, pages with successful primary and secondary OCR, review count, job counts, running `active_jobs` (`stage`, zero-based `page_index`, `attempts`), and failed-job details. The process command is synchronous; polling this persisted job state provides live page/stage progress.
- Flutter starts `scribeforge process ... --cancelable`; the child reads newline-delimited `{"schema_version":1,"type":"cancel"}` from stdin. It exits with `130` after cancelling, and the UI refreshes job status so the failed/incomplete stage can be retried.
- `scribeforge review --store <store> --json` emits `schema_version: 1` with project IDs and queued items. Each item includes zero-based page/pair indexes, risk score/reasons, optional `furniture_reason`, primary/secondary/VLM text, VLM uncertainty, crop and source-image paths, geometry boxes, and alignment conflicts. The review screen can opt into furniture rows without confirming them; export offers a separate keep-furniture option.
- `scribeforge resolve --store <store> --project-id <id> --page <n> --pair <n> --text <literal>` explicitly targets the project and appends a human decision. `--project-id` remains optional for older single-project CLI usage.
- `scribeforge audit --store <store> --json` checks a project read-only and returns the SQLite/page/source findings used by **项目安全**.
- `scribeforge backup --store <store> --out <zip> --json` creates and internally verifies a snapshot archive. The Flutter client omits `--overwrite`; output names are unique and existing files are not replaced.
- `scribeforge verify-backup --backup <zip> --json` validates archive paths, hashes, and SQLite integrity before the UI enables restore.
- `scribeforge restore --backup <zip> --to <new-store> --json` verifies again and restores atomically into a destination that must not already exist.

- `scribeforge runtime-status --json` reports the managed runtime root, hardware, bootstrap-tool availability, and per-backend state without changing anything.
- `scribeforge setup --json [--backend mineru|paddleocr]` writes newline-delimited schema-version-1 messages to stdout. `progress` records include backend, phase, state, step name, and completed/total step counts; the final `result` contains success, any actionable failure message, and both backend states. `--backend` repairs only that backend; without it, both are configured. The legacy CLI-only Tk wizard remains the default when `--json` is omitted.

The JSON contract is versioned; unknown schema versions or malformed evidence are rejected instead of guessed. Additive fields can be ignored by clients that do not use them. Page and pair indexes in CLI/JSON are zero-based; the UI displays both page numbers and pair numbers from one.

## Development checks

```bash
cd clients/flutter_desktop
dart format lib test
flutter analyze
flutter test
```

The Python CLI and desktop UI can also be tested independently from the repository root with `python -m pytest`.

### Review a long queue

In the review workspace, **筛选与排序** can narrow the current view to a physical
page range, literal candidate text, a risk reason, evidence availability or
session marks. **浏览当前队列** jumps to a displayed pair without recording a
decision. Hidden pairs still count toward pending review and can still block
trusted export. Marks and filters are temporary and reset when switching books.

**放大或比较图像证据** opens the saved crop, original page, or the available OCR
coordinate frame. Zoom and pan do not change evidence. When comparing frames,
select one pane to zoom it; the viewer does not infer or overlay a mapping
between the original and preprocessed frames. Short and narrow windows use the
page menu in the app bar instead of the side rail.

### Review decision history

Open **复核 → 决策历史** even after the pending queue is empty. The history is
read-only and includes human, automatic, and VLM decisions. Filter by physical
page, pair, or actor; open a record to see accepted text, saved risk and evidence
references. A reference to the current verification batch does not imply that
this row is the final export decision. Compare only records that cite the same
verification batch, since a pair index can refer to different text after reruns.

Desktop review requires a backend that returns `review_token` in `review --json`.
A write with that token is refused if OCR verification or the VLM reading has
changed or if a decision was already recorded for that pair. A refreshed queue
never carries an old-version draft into newer evidence. The CLI provides
`review-history --store DIR --project-id ID`, which returns schema-versioned JSON;
pass `--snapshot-id` and `--before-id` to continue a list or `--decision-id` to
read an individual record. This command does not migrate or write the database.

### Browse saved OCR evidence

Open **书籍 → 浏览 OCR 证据** to browse physical pages even when the review queue
is empty. Select a page to see the OCR engines, versions, input frame metadata
and current or historical verification batches. Raw lines display saved text,
normalized input coordinates and minimum reported token confidence. Verification
batches display ordered line members, risk, conflict counts and ambiguity;
these are evidence, not the final reading order or accepted transcription.

The read-only `ocr-evidence --store DIR --project-id ID --section pages` command
uses physical page indexes (zero-based). Use `--section page --page N` for
batch IDs, `--section lines --page N --run OCR_ID` for raw lines, and
`--section pairs --page N --run VERIFICATION_ID` for alignment. List sections
return `next_after` for `--after`. Text and large groups are explicitly marked
when previewed; there are no database migrations or decision writes.

### Search saved evidence across a book

From **书籍 → 浏览 OCR 证据 → 全书证据检索**, search either raw OCR
lines (including alternate runs) or saved decision records. Optional physical
page bounds use one-based numbering in the app. Decision searches may be
filtered by actor. Matching is literal and case-sensitive; it does not convert
variants or match across records. Open a result for longer context and its
stored provenance. The latest-record marker does not certify export eligibility.

The CLI equivalent is `evidence-search --store DIR --project-id ID --source ocr
--term=TEXT`. For decisions, choose `--source decisions` and optionally pass
`--actor`. CLI physical page indexes are zero-based. Pass `--snapshot-id` and
`--before-id` from the response to page through the same result sequence; use
`--record-id` with the snapshot to retrieve a longer excerpt. This command is
read-only and never invokes OCR or sends content to a remote model.

### Check trusted export before writing files

Open **导出 → 导出前检查**. The check uses the same content assembly and strict
acceptance gate as trusted export. It lists unresolved pairs, unverified pages,
unconfirmed textless pages, evidence failures and spot checks. Select a physical
page to narrow the issue list or follow a link into review, page evidence,
decision history, processing or project safety. Filtering does not change the
whole-book verdict. An ink measurement is not permission to mark a page blank.

The matching CLI command is `export-readiness --store DIR`, with optional
`--strict-acceptance`, `--include-page-furniture`, and `--page N` (zero-based).
It returns JSON and exits successfully for a completed assessment even if the
report is blocked; inspect `ready` and `blocking_count`. The check does not
migrate the store or write exports. A pass does not validate output metadata,
destination paths or file writing, and the actual export always checks again.

### Confirm a textless page after inspecting the original

From **导出 → 导出前检查 → 无文字页待确认 → 核对原页并确认**, inspect
and zoom the original saved PNG or JPEG, choose blank or non-text illustration,
and give a reason. If the scan contains missed text, return to processing instead.
The app checks the preview digest before showing it and the core rechecks the
verification and raster before appending the human confirmation. A later OCR
verification makes that confirmation historical; it is not reused by export.

The `textless-preview` CLI returns view evidence and recent confirmation history.
Use its token with `confirm --project-id ID --confirmation-token TOKEN --json` to
bind a write. Preview requires saved digest evidence; audit/rehash older projects
first. Existing unguarded CLI confirmations retain their prior semantics. Stores
with new bound confirmations use schema 16; keep a backup before downgrading.

### Verify an exported EPUB or DOCX

Open **导出 → 校验已导出的文件**. Keep the book, `.receipt.json` and
`.normalization.json` together. The check compares local byte counts and
SHA-256 fingerprints and can compare the receipt's recorded source fingerprint
with the current project's source. For older exports it reports separately
whether only a normalization report matches or no receipt is available.

`verify-export --file PATH` performs the same read-only check without opening
the book archive. Optionally pass `--source-sha256 DIGEST`. It returns a JSON
assessment; inspect `status` even if the command exits zero. A local receipt is
not a signature, does not verify the newest project decisions, and cannot prove
provenance if all companion files were replaced together. If a write was
interrupted, re-export to a new path and retain the original files for review.

### 对比已保存的处理报告

在「处理 → 处理报告」分别导出基准和新版 JSON，再选择「对比已保存报告」。
主页展示项目与复核计数、阶段耗时和风险原因变化；「页面」可按耗时、复核、
状态和计时覆盖筛选。点击一页查看各阶段状态、尝试次数和风险原因。

对比只读取两份文件（每份最多 16 MiB），不打开或修改项目。计时代表最近
一次尝试的累计值；计时口径或覆盖不同的记录不计算对应耗时差。零秒是有效值，
“—”表示未测量、缺失或不可比较。标题、编号和页码不能证明来源相同；标签或
页数不一致时默认只看汇总，核对后可手动按页码比较。换页、筛选和排序时会
核对文件的 SHA-256；文件被替换后可用「重新读取文件」开始新的对比。

CLI：`scribeforge compare-processing-reports --baseline before.json --candidate after.json`
输出 JSON。`--filter` 和 `--order` 控制页面明细，不影响汇总；下一批使用
`next_offset` 和两个响应中的 SHA-256（`--baseline-sha256`、`--candidate-sha256`）。

### 工作台的布局与视觉约定

工作台采用浅纸色画布、白色内容卡片和深绿主操作；提示和错误有独立图标与文字，
不是只靠颜色表达。中文使用本机字体回退，Windows 优先微软雅黑 UI，不需要从网络
下载字体。页面标题、表单、候选证据和提示分别使用固定的文字层级和 4 点间距节奏。

宽屏显示带文字的侧栏，导出信息与版式选项并排，复核的双 OCR 候选并排。中间尺寸
保留窄侧栏，窄窗或矮窗改用左上角菜单；内容按可用宽度堆叠，放大文字时也会切换
为单列。切换布局不会重新创建活动项目或丢弃正在填写的表单。长操作提示可用
「查看完整消息」展开并复制。识别、证据、决策和导出的约束没有改变。
