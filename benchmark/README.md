# Benchmark

This directory contains manifests and small redistributable fixtures only. Do not commit copyrighted books or private source scans.

The first real benchmark should contain 30–50 representative pages with manually verified ground truth spanning clear/poor scans, chapter starts, punctuation, footnotes, images, headers/footers, and Chinese/English/Japanese samples.

## Current corpus

| Path | Content |
| --- | --- |
| `corpus/v/` | 6 hand-transcribed pages from `V..pdf`: publisher note, translator preface, a chapter-title page with a centred poem, and three body pages with footnotes and dialogue |
| `analysis/` | PDF characteristic and book-scale measurement tooling plus recorded results |
| `benchmark_report.md` | Report skeleton; accuracy cells stay pending until real backends run |

`corpus/v` is a **baseline regression corpus for modern horizontal print**, not an
ancient-book corpus. It cannot validate vertical text, traditional characters or
degraded scans. Add such material (public domain or licensed) before claiming
ancient-book support — see [`docs/ancient-book-support.md`](../docs/ancient-book-support.md).


Required metrics: CER, WER, punctuation accuracy, missing/extra line rate, reading-order errors, wall time, pages/minute, peak RAM/VRAM when measurable.

Compare:
A. MinerU only
B. PaddleOCR only
C. MinerU + PaddleOCR disagreement pipeline
D. C + VLM review of conflict crops

## Running the engine benchmark

Variants A and B are runnable today; use the optional flags below for C and D:

```bash
# Score both managed backends against a corpus manifest.
scribeforge benchmark --manifest benchmark/manifest.json --out benchmark/benchmark_report.md

# Score A, B and C (dual-engine pipeline decisions) in one report.
scribeforge benchmark --manifest benchmark/manifest.json --out benchmark/benchmark_report.md --include-pipeline

# Score one backend only; the other runtime is not required.
scribeforge benchmark --manifest benchmark/manifest.json --out benchmark/benchmark_report.md --engine mineru

# Score A, B, C and D through a local OpenAI-compatible VLM endpoint.
scribeforge benchmark --manifest benchmark/manifest.json --out benchmark/benchmark_report.md \
  --include-pipeline --include-vlm \
  --vlm-endpoint http://127.0.0.1:1234/v1 --vlm-model your-local-vision-model

# Use a corpus and a runtime root outside the repository.
scribeforge benchmark \
  --manifest D:/corpus/manifest.json \
  --root D:/corpus \
  --runtime-root D:/scribeforge-runtime \
  --out benchmark/benchmark_report.md
```

`--root` defaults to the manifest directory, `--runtime-root` to `<root>/runtime`, and `--engine` is repeatable (default: `mineru`, then `paddleocr`).

The command:

1. loads and validates the manifest;
2. checks every case before starting an engine: manifest image and ground-truth paths must be relative and resolve inside the selected corpus root (absolute paths, parent traversal and symlinks escaping the root are rejected); missing or empty images and missing or blank UTF-8 ground truth are all reported, so a typo cannot silently shrink the corpus;
3. builds only the requested backends from managed runtimes; an unavailable backend fails loudly instead of degrading evidence;
4. scores each requested engine for CER, WER, punctuation accuracy, missing/extra lines, per-case duration, and pages/minute;
5. with `--include-pipeline`, also processes every case through the production alignment, risk assessment, verification, and conservative decision stages in an isolated temporary SQLite project;
6. scores only text represented by accepted decisions. Unresolved disagreements are never guessed or silently copied from either engine; accepted/unresolved pair counts appear in both the aggregate and per-case tables. The report includes the corpus path, manifest SHA-256, a corpus-content SHA-256 covering the manifest plus every referenced image and ground-truth file, aggregate rows, and per-case metrics. The runner recomputes the fingerprint after scoring and refuses to write a report if the corpus changed during the run.

Exit codes: `0` report written; `2` missing or invalid manifest, corpus problems, corpus mutation during scoring, unknown or unavailable backend, engine failure, or an unwritable destination.

The report is Markdown because corpus pages and transcriptions stay outside the repository; only redistributable fixtures may be committed.

## Status

- A (MinerU) and B (PaddleOCR) are implemented and covered by offline tests with deterministic fake engines. Reports count pairwise reading-order inversions among exactly matched lines; omissions and extra lines remain separate metrics.
- C is available through `--include-pipeline`; it uses the production decision layer and explicitly scores unresolved pairs as omissions while reporting accepted/unresolved coverage.
- D (C + VLM review) is runnable through an explicitly configured OpenAI-compatible vision endpoint. Loopback endpoints are allowed by default; remote endpoints require HTTPS and --allow-remote-vlm. Only generated review crops are sent. ScribeForge does not install or silently start a model server, so model lifecycle remains an explicit user choice.
- `benchmark_report.md` stays "pending" until it is generated by `scribeforge benchmark` from legally usable pages with hand-verified ground truth. Never hand-edit accuracy numbers into it.
- Reports include the manifest's declared `ground_truth_review_status` and `ground_truth_note`. A missing status is shown as `not declared`; the runner never infers that a transcription is double-proofread.
- `benchmark_report.md` is currently pending: the recording environment had no GPU
  and no access to model hosting, so no A/B/C/D accuracy run was possible. The
  corpus and the reproduction command are committed; numbers are never filled in
  by hand.
