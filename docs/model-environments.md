# Model Environments

ScribeForge keeps heavyweight OCR runtimes outside the core environment.

## MinerU worker

Use a dedicated Python environment. MinerU 4.x supports Python >=3.10,<3.15; use a separate 3.12 environment for this backend. Install a tested 4.x release and expose `mineru-kit` on that worker environment's PATH.

## PaddleOCR worker

Use a second dedicated environment. ScribeForge targets local inference and does **not** use the hosted `paddleocr api` command. The initial adapter targets PP-OCRv6 because current PaddleOCR documentation identifies it as the latest universal OCR generation with unified Chinese/English/Japanese coverage.

Exact CLI/result-shape compatibility must be pinned by adapter contract tests before a runtime version is promoted to supported status.

## Privacy invariant

Workers receive a local image path and return OCR JSON through stdout. Network/cloud OCR is never silently selected as a fallback.
