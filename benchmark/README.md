# Benchmark

This directory contains manifests and small redistributable fixtures only. Do not commit copyrighted books or private source scans.

The first real benchmark should contain 30–50 representative pages with manually verified ground truth spanning clear/poor scans, chapter starts, punctuation, footnotes, images, headers/footers, and Chinese/English/Japanese samples.

## Licensing and provenance

Every manifest must declare a top-level `license` (SPDX id or `public-domain`)
and optional `license_url`; each case may declare a `source` (archive, URL,
donor). `benchmark --check-only` fails when the license is missing and warns
on non-standard license strings or missing per-case sources. Never commit
copyrighted books or private scans.

## Quality gate

`scribeforge benchmark --manifest <m.json> --out <gate.md> --check-only` runs the
corpus quality gate without scoring any engine (add `--quality-json` for JSON).
It fails (exit 2) when the corpus is below 30 pages, misses a required language
(zh/en/ja), has fewer than 8 pages per required language, lacks a vertical
Japanese sample, or reuses an image path. The same coverage warnings are embedded
in every rendered benchmark report under "Corpus coverage warnings".

## Synthetic multilingual fixture (CI harness, not accuracy data)

`src/scribeforge/benchmark/multilingual_synthetic.py` builds a deterministic
zh/en/ja corpus (en two-column, ja vertical-rl, CC0-1.0) with an oracle engine
binding, so the multilingual report/JSON strata and the vertical
reading-order metric are exercised in tests without real scans. These numbers
measure pipeline mechanics only — never report them as OCR accuracy.

## Building and tracking a multilingual corpus

`analysis/build_corpus.py` rasterises chosen PDF pages and writes blank
transcriptions that block scoring until a human fills them in. Pass
`--page-spec spec.json` to override language/direction/tags per physical page
(`{"pages": {"12": {"language": "ja", "direction": "vertical-rl", "tags":
["vertical"]}}}`), so one source PDF can seed a mixed zh/en/ja corpus. Then
track progress with `scribeforge benchmark --manifest <m.json> --out status.md
--annotation-status` (add `--quality-json` for JSON): it reports pages per
language and per direction, empty/missing ground truths, and the quality-gate
verdict, and exits 2 while annotations are incomplete — so corpus growth toward
the 30-50 page / >= 8 pages per language target is measurable in CI.

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


Required metrics: CER, WER where applicable, punctuation accuracy, strict LCS missing/extra lines, changed lines, net missing/extra lines, reading-order errors, wall time, pages/minute, peak RAM/VRAM when measurable.

### Metric semantics

- **CER is primary for Chinese and Japanese.** Chinese (`zh*`, `cmn`, `yue`, `lzh`) and Japanese (`ja*`) cases, plus unknown (`und`) language, report WER as `N/A` with tokenizer `not-applicable`; ScribeForge does not invent a word segmenter for them. Other languages use the explicit `whitespace-v1` tokenizer: `str.split()` over Unicode whitespace, preserving case, punctuation and hyphenation. WER values produced with another tokenizer are not directly comparable. A Chinese WER above 1.0 has no recognition meaning — with whitespace "words" an unsegmented Chinese line is one giant token, so one inserted line can add more edits than the reference "words"; that is a tokenizer artifact, which is exactly why those cases are N/A. Wherever WER is applicable, a value above 1.0 simply means insertions outnumber reference words.
- **Macro and micro rates answer different questions.** Macro CER/WER weight each applicable case equally. Micro CER/WER are `sum(edit distance) / sum(reference characters or words)`, so long pages have proportionally more weight; the report displays the exact edit/reference-unit counts alongside each micro rate. Raw counts missing from legacy, manually constructed scores make the affected micro rate N/A rather than being reconstructed from a rounded percentage. Empty references retain the existing convention: zero edits give rate 0, while any edits give rate 1.
- **Aggregate WER discloses both denominators.** Its macro rate averages applicable cases only and is rendered with `(applicable / total)` case counts; its micro rate sums edits and reference words over those same applicable cases. `None`/N/A values are never coerced to zero. WER can exceed 1.0 when insertions outnumber reference tokens; that alone is not an implementation error.
- **Every macro and micro rate carries a deterministic confidence interval** rendered as `[lower, upper]` at the declared level (95% by default). Per-case and micro rates use the Wilson score interval over their raw edit counts; macro rates use the normal approximation over per-case rates and need at least two cases — a single case renders the bare mean. With zero reference units there is no count-based interval. Intervals for rates above 1.0, which WER can reach through insertions, are approximate because the binomial model no longer applies.
- **Manifest tags provide non-exclusive strata.** The report recomputes the same aggregates for every declared tag and adds `(untagged)` for cases without labels. A multi-tag case contributes to every applicable row, so tag rows must not be added together as a partition. Tags are corpus metadata, not inferred layout classes.
- **Results are always stratified by declared language.** The report header lists the declared languages with case counts, and a mandatory `Results by language` table recomputes the same macro/micro rates (with confidence intervals) plus reading-order errors and the reading-order error rate per language. Languages are manifest metadata: a language without corpus cases has no row, so claims about unsupported languages (English, Japanese) stay visible as an absence rather than being averaged away.
- **Reading-order error rate is a first-class metric.** `RO err rate` is reading-order inversions per reference line (raw inversions divided by reference line count, using the zero-reference convention: empty reference with no inversions is a defined 0.0). It appears in the variants table, the per-case table, the per-language table, and the per-direction table.
- **Writing-direction strata appear whenever a case declares `direction`.** The `Results by writing direction` table aggregates CER, unresolved pairs, reading-order errors and RO err rate per declared direction, even when only one direction is declared, so a fully vertical corpus still gets its row.
- **Vertical text gets its own reading-order section.** Whenever any case declares a `vertical-*` direction, the report renders a `Vertical text reading order` section with per-variant vertical case counts, mean CER, reading-order errors and RO err rate. Reading order is the core vertical-layout metric: an engine that recognizes every character but reads columns left-to-right still fails the page. `manifest.example.json` models the required shapes: an English `two-column` case and a Japanese `vertical-rl` case.
- **Machine-readable output.** `scribeforge benchmark ... --report-json report.json` additionally writes the run as schema-v1 JSON. Its `by_language` strata are mandatory (an unverified language is an absent row, never an inferred zero), and `by_direction` / `vertical_reading_order` carry the reading-order error rate; both are empty lists when no case declares a direction.
- **Strict line counts remain available.** LCS Missing/Extra are ordered, exact whole-line differences. Changed greedily pairs unmatched lines one-to-one when `SequenceMatcher.ratio >= 0.5`; ties are deterministic. Net Missing/Extra subtract Changed from the respective strict LCS count. A one-character line substitution therefore has LCS 1/1, Changed 1 and Net 0/0, while unrelated deletion/insertion lines remain net differences.
- **Changed is textual evidence, not layout analysis.** It does not use coordinates and cannot prove physical region omission, reading-order correctness or geometric alignment.
- **Spatial Missing/Extra answer the geometric question.** When a case declares reference line boxes (the oracle ceiling does, via deterministic synthetic layout), the report greedily pairs reference boxes with the engine's reported line boxes one-to-one at IoU >= 0.5: unmatched reference boxes are Spatial Missing, unmatched hypothesis boxes are Spatial Extra. Without declared reference geometry these columns render `-` — geometry is never inferred from text. A box match does not prove the text matched; read the spatial counts together with strict LCS Missing/Extra, so a one-character line error shows up as Changed, not as a spatially missing line.

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
4. scores each requested engine with the language-aware metrics above, punctuation accuracy, strict/changed/net line differences, per-case duration, and pages/minute;
5. with `--include-pipeline`, also processes every case through the production alignment, risk assessment, verification, and conservative decision stages in an isolated temporary SQLite project;
6. scores only text represented by accepted decisions. Unresolved disagreements are never guessed or silently copied from either engine; accepted/unresolved pair counts appear in both the aggregate and per-case tables. The report includes the corpus path, manifest SHA-256, a corpus-content SHA-256 covering the manifest plus every referenced image and ground-truth file, aggregate rows, and per-case metrics. The runner recomputes the fingerprint after scoring and refuses to write a report if the corpus changed during the run.

Exit codes: `0` report written; `2` missing or invalid manifest, corpus problems, corpus mutation during scoring, unknown or unavailable backend, engine failure, or an unwritable destination.

The report is Markdown because corpus pages and transcriptions stay outside the repository; only redistributable fixtures may be committed.

## Comparing runs (regression gate)

Every scored run can also be written as schema-v1 JSON with `--report-json`. Two such files — a baseline and a current run — can be compared metric by metric (aggregate, per-language, per-direction, vertical reading order, per-case) with:

```bash
# Write both runs as JSON.
scribeforge benchmark --manifest benchmark/manifest.json --out report.md --report-json run-new.json

# Compare against a stored baseline; Markdown to stdout.
scribeforge benchmark-compare run-baseline.json run-new.json

# CI gate: exit 3 when CER/WER/reading-order regress beyond threshold.
scribeforge benchmark-compare run-baseline.json run-new.json \
  --fail-on-regression --cer-pp 0.5 --wer-pp 1.0 --ro-pp 1.0 --out regression.md

# Machine-readable comparison.
scribeforge benchmark-compare run-baseline.json run-new.json --json --out regression.json
```

Exit codes: `0` comparison written and (with `--fail-on-regression`) no gated regression; `2` input file missing, invalid JSON or wrong schema; `3` a gated metric regressed beyond its threshold. Metrics that are N/A on either side (for example WER for zh/ja) are reported as `new` / `unavailable` / `missing` / `na` with a null delta — they are never read as 0 and never fail the gate.

## Tracking trends over repeated runs

Append every scored run to a JSONL history and analyze metric trajectories over time:

```bash
# Append each run's schema-v1 JSON to a history file (one line per run).
scribeforge benchmark --manifest benchmark/manifest.json --out report.md --history benchmark/history.jsonl

# Trend of the default metrics (mean CER, mean WER, reading-order rate), Markdown.
scribeforge benchmark-trend benchmark/history.jsonl

# A specific engine/metric/scope, as JSON.
scribeforge benchmark-trend benchmark/history.jsonl \
  --engine mineru --metric mean_cer --scope language:zh --json --out trend.json

# CI gate: exit 3 when the last 3 runs all move a metric adversely by > 1pp total.
scribeforge benchmark-trend benchmark/history.jsonl --fail-on-trend --window 3 --max-adverse-pp 1.0
```

`--scope` is one of `aggregate`, `language:<code>`, `direction:<dir>`, `case:<id>` or `vertical_reading_order`. Exit codes: `0` trend rendered and (with `--fail-on-trend`) nothing degrading; `2` invalid history file or arguments; `3` a metric's last `--window` deltas are all adverse beyond `--max-adverse-pp`. N/A values (for example WER for zh/ja) are skipped when computing deltas — never read as 0, never counted as adverse.

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
