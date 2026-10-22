# ScribeForge Flutter Desktop

This is the Flutter-first comparison workspace for the original scan and separate OCR/EPUB candidate outputs. The Python/Tkinter shell remains available for evidence-level proofreading, but this package is the intended main desktop UX for inspecting and judging whole-book output.

## Requirements

- Flutter 3.47.7 or newer in the 3.47 stable toolchain family (Dart 3.13+).
- Windows 11, macOS, or Linux desktop development support.
- A local PDF source and one or two EPUB candidate files.

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

## Prepare model candidates with Python

After a project has completed both OCR runs, export the persisted raw outputs to explicitly unverified EPUBs:

```bash
scribeforge export-candidates --store path/to/book.scribeforge --out-dir comparison-candidates
```

The command produces `candidate-mineru.epub`, `candidate-paddleocr.epub`, and `candidate-manifest.json`. It fails rather than making a partial book if a requested engine has no saved OCR output for a page. These files are marked **UNVERIFIED OCR CANDIDATE** and must not be confused with accepted exports.

Open the original PDF and both candidate EPUBs in the Flutter workspace. The PDF and each candidate are navigated by source-page index. ScribeForge candidate EPUBs use `page-000001.xhtml`-style filenames, so the matching is explicit; generic chapter-based EPUBs use spine order and are flagged as approximate. Candidate text is extracted from EPUB XHTML and compared line-by-line; this first slice focuses on content fidelity, not pixel-perfect EPUB CSS rendering.

## Human evaluation

Choose a better candidate per source page, optionally pick an overall book winner, and add notes about omissions, punctuation, reading order, or layout. The evaluation is saved beside the source PDF as `<file>.pdf.scribeforge-review.json`, including SHA-256 fingerprints of the PDF and candidate EPUBs. If an input changes or disappears, stale ratings are not silently reused.

No score or winner is inferred automatically: the line diff is a navigation aid, not a measure of OCR or literary quality. The verified `scribeforge export` path continues to export only accepted decisions; candidate EPUBs are a separate review layer.
