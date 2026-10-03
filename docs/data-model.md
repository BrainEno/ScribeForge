# Data Model

SQLite is the source of truth for project state. Original source files remain immutable.

Core entities:
- `projects`: book identity, source URI/hash, status, timestamps
- `pages`: project/page index, source image reference, dimensions, preprocessing metadata
- `engine_runs`: engine/version/config, page, status, timings
- `blocks`, `lines`, `tokens`: hierarchical OCR geometry and text evidence
- `alignments`: relationships between evidence produced by independent engines
- `risks`: reason codes, score, target evidence
- `review_candidates`: engine/VLM candidate readings
- `decisions`: accepted text, actor (automatic/VLM/human), timestamp, rationale/evidence refs
- `jobs`: stage, page scope, state, attempts, error, timing
- `exports`: format, verified revision, path/hash

Coordinates use a normalized page coordinate system in addition to engine-native geometry.

A decision never deletes engine evidence. Corrections create/replace the decision layer while source OCR remains immutable.

## Implemented persistence slice

Schema version 1 now persists `projects`, `pages`, `engine_runs`, `ocr_lines`, `ocr_tokens`, and append-only `decisions` with foreign-key protection. Recording another OCR pass creates a new engine run rather than updating old OCR rows. Decision history stores explicit evidence references and does not mutate source OCR text.

The remaining entities (`alignments`, `risks`, `review_candidates`, `jobs`, and `exports`) stay on the planned schema and will be added in versioned migrations rather than by destructive database resets.
