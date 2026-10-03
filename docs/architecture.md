# Architecture

## Goal
Convert scanned literary books into verified structured text with provenance. Accuracy is achieved by disagreement detection and evidence review, not by trusting a single model.

## Layers
1. **Domain** — engine-neutral page/block/line/token evidence, decisions, risk.
2. **Application** — resumable jobs and pipeline orchestration.
3. **Adapters** — MinerU, PaddleOCR, later Surya/VLM.
4. **Persistence** — SQLite project database plus immutable source assets.
5. **Export** — EPUB 3 and DOCX from verified text only.
6. **Desktop UI** — later; communicates with workers rather than loading models in the UI process.

## Pipeline
IMPORT -> PREPROCESS -> PRIMARY_OCR -> SECONDARY_OCR -> ALIGN -> RISK -> VLM_REVIEW -> HUMAN_REVIEW -> EXPORT

Every stage is idempotent or records enough state to resume safely. Page-level work is committed incrementally.

## Runtime isolation
Model dependencies are expected to conflict. Each backend implements the same contract and may run in a separate Python environment/process. The core exchanges versioned serializable results.

## Text layers
- source OCR: immutable engine output
- normalized: mechanical Unicode/layout normalization only
- verified: evidence-backed accepted transcription
- editorial: optional future author/user edits

No downstream layer may mutate upstream evidence.
