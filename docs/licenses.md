# License Inventory

ScribeForge itself is released under the **Apache License 2.0** (see
[`LICENSE`](../LICENSE), SPDX: `Apache-2.0`). This page inventories the
licenses of the **runtime dependencies the planner downloads on demand**,
because those are *not* covered by the project license and matter for
redistribution and institutional compliance (issue #90).

## Project

| Component | License | Notes |
| --- | --- | --- |
| ScribeForge source code | Apache-2.0 | `LICENSE` at the repository root; declared in `pyproject.toml` |

## OCR / layout engines (downloaded at runtime, not bundled)

| Engine | Role | Code license | Model weights | Notes |
| --- | --- | --- | --- | --- |
| MinerU | primary OCR + layout | Apache-2.0 | Apache-2.0 | planned backend `mineru` |
| PaddleOCR (PP-OCRv6) | secondary OCR | Apache-2.0 | Apache-2.0 | planned backend `paddleocr` |
| PaddleOCR-VL | strong recognition candidate | Apache-2.0 | Apache-2.0 | optional backend `paddleocr-vl`, installed on demand via `repair` |
| Surya | layout / reading-order second opinion | Apache-2.0 | **OpenRAIL-M** | optional backend `surya`; weights are **not** Apache-2.0 |

**OpenRAIL-M warning (issue #90):** Surya's (and Marker's) model weights are
licensed under OpenRAIL-M, which is *not* a pure OSI open-source license. If a
future release bundles model files instead of downloading them at runtime, the
weights must be labeled separately with the OpenRAIL-M terms and use
restrictions. Downloading them at runtime (the current behavior) does not
redistribute them.

## Benchmark corpus fixtures

Benchmark fixtures under `benchmark/` come from the Apache-2.0 licensed
`open-guji/open-guji-cv` test fixtures; see
[`benchmark-corpus.md`](./benchmark-corpus.md). Do not add copyrighted scans.

## Python dependencies

Core runtime dependencies (Pillow, pypdfium2, numpy, opencc) carry
permissive licenses (MIT / Apache-2.0 / BSD-style); the full list with exact
versions is reproducible from the locked requirement files under
`src/scribeforge/runtime/locks/` and from `pip show <package>` in any
installed environment.
