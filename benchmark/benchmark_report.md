# Benchmark Report

- corpus: `benchmark/corpus/v/manifest.json`
- manifest sha256: pending
- generated: pending — **no real OCR backends were available in the recording environment**
- variants: pending

> **This report intentionally contains no accuracy numbers.**
> Accuracy requires the real MinerU / PaddleOCR runtimes, which were not installed
> in the environment that produced this file. The corpora, the harness and the
> one-command reproduction path are committed; the scores are not invented.
> Run the command in [Reproduce](#reproduce) on a GPU machine to replace this file.

## Corpus

`benchmark/corpus/v/` — 6 pages sampled from `benchmark/V..pdf`
(Thomas Pynchon, *V.*, Chinese translation, Yilin Press 2008; 562-page
DuXiu/SuperStar scan, 2481 × 3728 px @ 300 DPI).

| Case | Physical page | Printed page | Section | Layout characteristics |
| --- | ---: | ---: | --- | --- |
| `v-010` | 10 | 5 | 出版说明 | Dense body, no indents kept by OCR engines |
| `v-014` | 14 | 9 | 译者序 | Inline Latin (`V.`, `Venus`), full-width parentheticals |
| `v-018` | 18 | 1 | 第一章 | **Sparse layout**: centred chapter title, centred poem lines, footnote |
| `v-020` | 20 | 3 | 正文 | Footnote markers ①②③, dialogue quotes, `V—诺特` dash |
| `v-030` | 30 | 13 | 正文 | Dialogue paragraphs, ellipsis `……`, footnote ① |
| `v-040` | 40 | 23 | 正文 | Dense nested quotes, em-dash parentheticals |

Note that printed page numbers **restart per section**: the file mixes three
independently numbered sequences plus blank separator pages. See
`docs/ancient-book-support.md` §6 P0-4 for why the pipeline currently has no
place to record that.

Ground truth is a single-pass human transcription of this scan. It is good
enough to detect regressions, but it has not been double-proofread against the
printed book, so it is a reference transcription rather than publisher-grade gold.

## Source PDF characteristics (measured)

| Attribute | Value |
| --- | --- |
| Total pages | 562 |
| Creator / Producer | DuXiu/SuperStar / AnnasArchive |
| Page raster @300 DPI | 2481 × 3728 px |
| Mean brightness | 240.1 / 255 |
| Mean dark pixel ratio | 4.73% |
| Mean page PNG | 1072 KB |
| Estimated full-book assets | ≈ 590 MB |
| Scan quality | Good clean modern print — **not** a representative ancient-book scan |

## Structural measurements (measured, not estimates)

Full data: [`analysis/measurements.json`](./analysis/measurements.json).

Import through the production `PdfPageSource` + `import_source` path:

| Metric | Value |
| --- | --- |
| 40 pages @300 DPI | 11.8 s |
| Seconds per page | 0.295 |
| Throughput | ≈ 204 pages/min |
| Assets | 41.9 MB (41.9 MB / 40 pages) |
| Peak RSS | 117 MB |

Orchestration → assembly → export at 562 pages, driven by **deterministic
synthetic engines** (explicitly not OCR; text output is `P0003L07`-style markers):

| Metric | Value |
| --- | --- |
| Orchestration | 51.6 s (0.092 s/page) |
| Assembly | 3.5 s |
| EPUB export | 0.07 s (198 KB) |
| DOCX export | 0.09 s (39 KB) |
| SQLite size | 66.8 MB |
| Decisions recorded | 14 050 |
| Peak RSS | 130 MB |

These are mechanism measurements only. They say storage, memory and export are
not the bottleneck at 800 pages; they say nothing about transcription quality.

## Accuracy variants

| Variant | Status |
| --- | --- |
| A — MinerU only | runnable, **not measured** |
| B — PaddleOCR only | runnable, **not measured** |
| C — dual-engine decisions (`--include-pipeline`) | runnable, **not measured** |
| D — C + VLM crop review (`--include-vlm`) | runnable with a configured endpoint, **not measured** |

Since issue #76 variant D only accepts a vision-model reading that corroborates one
of the engines; a model answer matching neither engine stays unresolved instead of
being taken on its own confidence. Variant D therefore reports *fewer* accepted
pairs than before, by design — the difference is a correctness fix, not a
regression, and it is not comparable to any earlier D number.

Disputed pairs can also be re-read before they are scored as unresolved:
`scribeforge repair` / `process --repair-rounds N` (see
[Self-comparison and self-correction](../docs/self-correction.md)). The benchmark
harness does not enable repair rounds, so variant C/D numbers stay comparable with
earlier runs; the repair yield per corpus is not measured yet.

## Pipeline text loss ceiling (measured end to end, not OCR accuracy)

`--oracle-ceiling` replays the corpus ground truth through two oracle engines and
the full production pipeline (alignment → risk → decisions → assembly). Scores
therefore contain **no recognition error**: whatever CER is left is text the
pipeline itself removed or reordered. It is a ceiling on quality, not a
transcription accuracy claim.

| Variant | Cases | Mean CER | Missing lines | Extra lines | Reading-order errors | Accepted pairs | Unresolved pairs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `oracle` (engine level) | 6 | **0.0000** | 0 | 0 | 0 | — | — |
| `oracle-ceiling` (full pipeline) | 6 | **0.0000** | 0 | 0 | 0 | 36 | 0 |

Reading: on these six real pages the pipeline loses nothing — furniture removal,
page labels, footnotes and adjudication do not eat body text, and every pair is
resolved. The end-to-end CER path itself is therefore proven to work; only real
model weights are missing for variants A–D.

A metric defect was fixed while measuring: a ground-truth file's trailing newline
was scored as one inserted character on every page, inflating a 0.5%-order CER by
roughly 20% relative. Scoring now runs through `scoring_text()` (CRLF, trailing
whitespace and document-edge blank lines normalised; interior blank lines kept).

```bash
python -m scribeforge.cli benchmark \
  --manifest benchmark/corpus/v/manifest.json \
  --out /tmp/ceiling.md --oracle-ceiling
```

### The same ceiling on real vertical woodblock pages

`benchmark/corpus/guji` holds three genuine vertical pages: the imperial preface
of 《欽定四庫全書簡明目錄》 (Qing Qianlong Wuyingdian woodblock, public domain),
8 columns of 21 characters per half-leaf, including a one-character column (奉)
and a two-character column (聖諭). The transcription is by hand, column by
column, right to left.

| Variant | Cases | Mean CER | Missing lines | Extra lines | Accepted pairs | Unresolved pairs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `oracle` (engine level) | 3 | **0.0000** | 0 | 0 | — | — |
| `oracle-ceiling` (full pipeline) | 3 | **0.0000** | 0 | 0 | 24 | 0 |

Three regression tests guard this number (`pytest -m benchmark`): the ceiling is
zero; reversing the transcription order must push CER above 0.2 (so the zero is
column order, not luck); and with the direction declaration removed,
`detect_page_direction()` still reports `vertical-rl` from column geometry alone.

## Paragraph reconstruction (measured on real corpus text — structural, not CER)

The metric above counts characters, so it cannot see a structural defect: text can
be 100% intact while the reader receives one paragraph per printed line. On the six
`corpus/v` pages the ground truth is 36 paragraphs; reconstructed as visual lines
at the book's 28-character measure it is **161 lines**, which is what the old
exporter emitted as 161 `<p>` elements, 14 of them ending mid-sentence.

`VerifiedPage.blocks()` now groups those lines with geometry and block labels
(indent, short line, line gap, sentence-final punctuation) instead of punctuation
alone. Measured by `tests/test_paragraphs.py::test_paragraph_recovery_on_the_real_corpus_text`:

| Page | True paragraphs | Recovered | Exact | Visual lines |
| --- | ---: | ---: | ---: | ---: |
| `v-010` | 5 | 5 | 5 | 28 |
| `v-014` | 4 | 4 | 4 | 30 |
| `v-018` | 11 | 11 | 11 | 15 |
| `v-020` | 3 | 3 | 3 | 28 |
| `v-030` | 9 | 9 | 9 | 30 |
| `v-040` | 4 | 4 | 4 | 30 |
| **total** | **36** | **36** | **36 (100%)** | **161** |

Zero characters lost. Two fixture defects had to be fixed before this number meant
anything, and both are worth keeping in mind for any future measurement:

1. a page can hold more rows than the fixture's synthetic page height allowed, so
   rows wrapped back to the top and scrambled reading order (`v-014`, `v-040`
   showed phantom paragraph errors);
2. breaking a paragraph at a fixed character count splits Latin words (`vect|or`),
   and the joiner then legitimately inserts a space, which looked like corruption.

This is a structural measurement, not an accuracy claim: there is still no OCR in
this environment. Cross-page continuation (page 30 ends `……特`, page 40 opens
`些爱。`) is **not** merged yet — it needs the chapter-level text flow from
issue #87. See [Paragraphs and structural fidelity](../docs/paragraphs.md).

## Preprocessing repair (measured, pixel-level — not CER)

`benchmark/analysis/preprocess_benchmark.py` derives deterministic degradations
from the real corpus pages (seeded, reproducible) and measures whether the
PREPROCESS stage detects and repairs them. Ink/paper separability is in grey
levels; residual skew is the worst of the 6 pages.

| Variant | Operations fired | Degraded | Repaired | Residual skew |
| --- | --- | --- | --- | --- |
| clean | none | 227.9 | 227.9 | 0.00° |
| skew-3deg | deskew | 226.6 | 226.1 | 0.00° |
| skew-6deg | deskew | 226.6 | 226.1 | 0.00° |
| spine-shadow | deshadow | 191.5 | **228.8** | 0.00° |
| faded | contrast | 0.0 | **228.6** | 0.00° |
| speckled | none (denoise off by default) | 221.8 | 221.8 | 0.00° |
| speckled+denoise | denoise | 221.8 | 224.7 | 0.00° |
| skew-and-shadow | deskew + deshadow | 192.0 | 222.8 | 0.50° |

The clean row is the important one: the default pipeline measures each page and
concludes that a healthy 300 DPI scan needs nothing, leaving the raster
byte-identical. Details and the three estimator defects found while measuring:
[`docs/preprocessing.md`](../docs/preprocessing.md).

Raw rows: [`analysis/preprocess_measurements.json`](./analysis/preprocess_measurements.json).

## Reproduce

```bash
scribeforge setup
scribeforge benchmark --manifest benchmark/corpus/v/manifest.json \
  --out benchmark/benchmark_report.md --include-pipeline
```

Then add `--include-vlm --vlm-endpoint http://127.0.0.1:1234/v1 --vlm-model <model>`
for variant D. Only crop images are sent; remote endpoints need HTTPS and
`--allow-remote-vlm`.

Preprocessing measurements are regenerated independently:

```bash
python benchmark/analysis/preprocess_benchmark.py
```

## Capability assessment

See [`docs/ancient-book-support.md`](../docs/ancient-book-support.md) for the full
review of whether ScribeForge can handle complex-layout, non-standard, low-quality
scans of 500–800 pages (ancient books, old editions), the prioritised gap list,
and the verified free/open-source technology recommendations.
