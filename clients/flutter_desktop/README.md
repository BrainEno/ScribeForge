# ScribeForge Flutter Desktop

This is the Flutter-first comparison workspace for the original scan and separate OCR/EPUB candidate outputs. The Python/Tkinter shell remains available for evidence-level proofreading, while this app focuses on whole-book comparison and human evaluation.

## Requirements

- Flutter 3.47.7 stable toolchain (Dart 3.13+).
- Windows 11, macOS, or Linux desktop development support.
- A local PDF source and one or two EPUB candidate files.
- To run OCR from the UI, install the Python CLI from the repository root with `python -m pip install -e ".[dev]"` and complete `scribeforge setup`.

## First-time setup

From this directory:

```bash
flutter create --platforms=windows,macos,linux --project-name scribeforge_desktop .
flutter pub get
```

Windows PowerShell users can run `./tool/bootstrap_desktop.ps1`; macOS/Linux users can run `sh tool/bootstrap_desktop.sh`. The generated `windows/`, `macos/`, and `linux/` folders are local platform runners. CI scaffolds them from Flutter templates before analyzing and testing, so generated native runner boilerplate does not obscure review of the product UI.

Run the app:

```bash
flutter run -d windows
# or: flutter run -d macos
# or: flutter run -d linux
```

## Run the OCR workflow from the UI

The top toolbar exposes three workflow actions:

1. **Set up OCR** starts the Python runtime/model setup wizard.
2. Open a source PDF, then choose **Run OCR** to invoke `scribeforge process`. The default project store is a sibling directory named `<pdf-stem>.scribeforge`.
3. Choose **Build A/B EPUBs** to call `scribeforge export-candidates`. The output folder is a sibling named `<pdf-stem>-comparison-candidates`; on success, MinerU and PaddleOCR EPUBs are loaded into candidate A and B automatically.

Command output and exit status appear in a log dialog. Exit code `1` means one or more pages failed and can generally be retried by running OCR again; exit code `2` indicates a fatal input or setup error. Candidate export fails when an engine has missing page output rather than silently exporting an incomplete book.

Use **Open project** to select an existing `.scribeforge` folder. The desktop app reads its saved project metadata and opens the recorded source PDF when that path is available. It verifies both the normalized source path and SHA-256 before enabling OCR, retries, or candidate export. If the source file has moved, changed contents, or the chosen folder contains multiple projects, processing is blocked and the mismatch is shown rather than silently reusing the old job history.

After opening a source PDF, the project-status strip reads the active store through `scribeforge status --json`. It shows imported pages, pages with successful results from both OCR engines, pending/running/succeeded/failed job counts, and the human-review queue. Use **Refresh project status** to reload it. When page jobs have failed and source identity matches, **Retry failed pages** runs the resumable `scribeforge process` command again; it does not delete previous evidence.

Use the settings icon in the upper-right corner to configure the CLI executable. The default is `scribeforge` on `PATH`; otherwise, enter the full path to the executable in the Python environment where ScribeForge is installed (for example, `C:\\ScribeForge\\.venv\\Scripts\\scribeforge.exe` on Windows). The value is stored in the current user's application configuration folder and is reused on the next launch. The app does not bundle the Python runtime or OCR model weights.

For a full novice walkthrough in Chinese, see [ScribeForge 新手使用指南](../../docs/user-guide-zh.md) and the repository [README](../../README.md).

## Prepare candidates manually with Python

After a project has completed both OCR runs, export the persisted raw outputs to explicitly unverified EPUBs:

```bash
scribeforge export-candidates --store path/to/book.scribeforge --out-dir comparison-candidates
```

The command produces `candidate-mineru.epub`, `candidate-paddleocr.epub`, and `candidate-manifest.json`. It fails rather than making a partial book if a requested engine has no saved OCR output for a page. These files are marked **UNVERIFIED OCR CANDIDATE** and must not be confused with accepted exports.

Open the original PDF and both candidate EPUBs in the Flutter workspace. The PDF and each candidate are navigated by source-page index. ScribeForge candidate EPUBs use `page-000001.xhtml`-style filenames, so the matching is explicit; generic chapter-based EPUBs use spine order and are flagged as approximate. Candidate text is extracted from EPUB XHTML and compared line-by-line; this slice focuses on content fidelity, not pixel-perfect EPUB CSS rendering.

## Human evaluation

Choose a better candidate per source page, optionally pick an overall book winner, and add notes about omissions, punctuation, reading order, or layout. The evaluation is saved beside the source PDF as `<file>.pdf.scribeforge-review.json`, including SHA-256 fingerprints of the PDF and candidate EPUBs. If an input changes or disappears, stale ratings are not silently reused.

No score or winner is inferred automatically: the line diff is a navigation aid, not a measure of OCR or literary quality. The verified `scribeforge export` path continues to export only accepted decisions; candidate EPUBs are a separate review layer.
