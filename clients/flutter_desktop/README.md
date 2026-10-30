# ScribeForge Flutter Desktop

The Flutter desktop client is the guided, single-book workflow for ScribeForge: import/open a project, run its resumable OCR pipeline, inspect the evidence-backed review queue, and export an accepted EPUB 3 or DOCX. Python owns OCR, persistence, and export; Flutter invokes the local `scribeforge` executable with argument lists and reads its versioned JSON contracts.

## Requirements

- Flutter 3.47.7 stable (Dart 3.13+).
- Windows, macOS, or Linux desktop development support.
- The Python CLI installed from the repository root:

  ```bash
  python -m pip install -e ".[dev]"
  ```

- OCR backends installed in ScribeForge's private runtime. **Settings → 运行时配置** opens the Flutter-native setup guide, streams per-backend/per-step progress, and can repair either backend independently. The development app does not bundle Python, OCR packages, model weights, or a VLM server.

## First-time setup and launch

From this directory:

```bash
flutter create --platforms=windows,macos,linux --project-name scribeforge_desktop .
flutter pub get
```

Windows PowerShell users can run `./tool/bootstrap_desktop.ps1`; macOS/Linux users can run `sh tool/bootstrap_desktop.sh`. The generated `windows/`, `macos/`, and `linux/` folders are local platform runners. CI scaffolds them from Flutter templates before analyzing and testing.

Run the desktop app:

```bash
flutter run -d windows
# or: flutter run -d macos
# or: flutter run -d linux
```

If the CLI is not on `PATH`, open **Settings → 命令行工具** and enter the full path to the executable in the Python environment where ScribeForge is installed.

## Guided workflow

1. **书籍** — Choose **导入书籍** for a PDF or page image. The default store is a sibling directory named `<source-stem>.scribeforge`. Choose **打开项目文件夹** to resume an existing single-book store.
2. **处理** — Run `scribeforge process`. The UI polls `status --json` while the command is active, showing pages with both OCR results, the active page/stage, job counts, and failed-page details. Processing is resumable; rerunning reuses successful page evidence and retries incomplete/failed work.
3. **复核** — Read both OCR candidates, any VLM reading and its uncertainty flag, risk reasons, and the crop (or original page image). Candidate buttons only copy text into the editor. Keyboard shortcuts **Alt+1/2/3** fill OCR 1/OCR 2/VLM candidates and **Ctrl+Enter** records the current text; recording is always an explicit human action that calls `resolve`, appends a decision, and never overwrites source OCR.
4. **导出** — Select EPUB 3 or DOCX, choose an output folder (the filename follows the book title and format), and set language plus optional author. Strict acceptance blocks export when unresolved content remains. Candidate OCR output is not an accepted final export.
5. **设置** — Inspect runtime and hardware status, launch the first-run setup wizard, refresh status, or configure the CLI executable.

Each project store is expected to contain one book. Before processing, the client checks the selected source path and SHA-256 against the project record. If a source has moved, **重新关联源文件** calls `relink-source`; it updates only the path after the source bytes match. A modified source is not silently applied to old jobs or OCR evidence.

The setup action is a native Flutter dialog rather than the legacy Tk window. It displays backend state, the current phase and step count, final failure details, and a one-backend repair action. Status inspection is read-only; **刷新运行时状态** re-reads the CLI report. Direct `scribeforge setup` without `--json` remains available as the CLI's Tk-based fallback. This desktop workflow does not automatically invoke a VLM; it displays previously recorded VLM evidence, if present, and never treats it as a human decision.

A prebuilt Windows installer is not yet published. The proposed packaging and release gates are documented in [`docs/windows-desktop-distribution.md`](../../docs/windows-desktop-distribution.md); until then, the development setup below still requires Flutter and Python tooling.

## Python CLI contract used by Flutter

The client communicates through local child processes, not a network service. It never parses human-readable `review` output for queue entries.

- `scribeforge status --store <store> --json` emits `schema_version: 1`. Each project includes total pages, pages with successful primary and secondary OCR, review count, job counts, running `active_jobs` (`stage`, zero-based `page_index`, `attempts`), and failed-job details. The process command is synchronous; polling this persisted job state provides live page/stage progress.
- `scribeforge review --store <store> --json` emits `schema_version: 1` with project IDs and queued items. Each item includes zero-based page/pair indexes, risk score/reasons, primary/secondary/VLM text, VLM uncertainty, crop and source-image paths, geometry boxes, and alignment conflicts.
- `scribeforge resolve --store <store> --project-id <id> --page <n> --pair <n> --text <literal>` explicitly targets the project and appends a human decision. `--project-id` remains optional for older single-project CLI usage.

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
