# Benchmark Report

- corpus: `benchmark\corpus\v\manifest.json`
- manifest sha256: `7bfa99fd304addd910dc65a941545abc266d6292520ef49deee67ebb6221af33`
- generated: 2026-10-20T10:33:02Z
- variants: 2

## Variants

| Variant | Cases | Mean CER | Mean WER | Punctuation | Missing | Extra | Elapsed s | Pages/min |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mineru | 4 | 0.0563 | 1.0987 | 0.5675 | 15 | 27 | 25.350 | 9.47 |
| paddleocr | 4 | 0.0879 | 3.6804 | 0.5096 | 15 | 108 | 59.896 | 4.01 |

## Per-case results

### mineru

| Case | Language | Tags | CER | WER | Punctuation | Missing | Extra | Seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v-020 | zh | body,footnote | 0.0505 | 0.9375 | 0.5672 | 7 | 7 | 6.197 |
| v-100 | zh | body,dialogue | 0.0903 | 2.2857 | 0.5810 | 2 | 14 | 6.413 |
| v-200 | zh | body,footnote | 0.0469 | 0.5714 | 0.6410 | 3 | 3 | 6.416 |
| v-300 | zh | body,dialogue | 0.0374 | 0.6000 | 0.4808 | 3 | 3 | 6.324 |

### paddleocr

| Case | Language | Tags | CER | WER | Punctuation | Missing | Extra | Seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| v-020 | zh | body,footnote | 0.0846 | 1.7500 | 0.4925 | 7 | 28 | 21.184 |
| v-100 | zh | body,dialogue | 0.1117 | 4.0000 | 0.5714 | 2 | 28 | 20.049 |
| v-200 | zh | body,footnote | 0.0764 | 3.5714 | 0.6282 | 3 | 25 | 11.864 |
| v-300 | zh | body,dialogue | 0.0787 | 5.4000 | 0.3462 | 3 | 27 | 6.798 |
