# Benchmark Report

Status: harness implemented and runnable for variants A/B; real corpus results pending.

Do not populate this report with synthetic accuracy claims. Results must come from manually verified, legally usable benchmark pages.

Generate it (never hand-edit the numbers) with:

```bash
scribeforge benchmark --manifest benchmark/manifest.json --out benchmark/benchmark_report.md
```

The report records the manifest SHA-256, one aggregate row per variant, and a per-case table including language and tags. See [README.md](./README.md) for corpus requirements and options.

## Planned comparisons

| Variant | CER | WER | Punctuation | Missing lines | Extra lines | Time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MinerU | pending | pending | pending | pending | pending | pending |
| PaddleOCR | pending | pending | pending | pending | pending | pending |
| Dual-engine diff | pending | pending | pending | pending | pending | pending |
| Dual + VLM review | pending | pending | pending | pending | pending | pending |

The benchmark runner will preserve per-case results so aggregate improvements cannot hide regressions on difficult page classes.
