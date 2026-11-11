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

A persistent **下一步** banner under the notice bar always shows the single
next action implied by the current state — configure the CLI (设置), one-click
初始化环境 on first run, 导入书籍, 重新关联源文件, 去处理, 去复核, or 去导出 —
with a button that jumps straight to the right page. The suggestion is computed
by a pure function (`nextStepGuidance`) with exhaustive unit tests.

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
