# Data Model

SQLite is the source of truth for project state. Original source files remain immutable.

Core entities:
- `projects`: book identity, source URI/hash, status, timestamps
- `pages`: project/page index, source image reference, dimensions, preprocessing metadata
- `engine_runs`: engine/version/config, page, status, timings
- `blocks`, `lines`, `tokens`: hierarchical OCR geometry and text evidence
- `verification_runs`: immutable comparisons between two OCR engine runs
- `alignments`: relationships between evidence produced by independent engines
- `alignment_conflicts`: character-range disagreements within an aligned pair
- `risks`: reason codes and score for an aligned pair
- `review_candidates`: risky evidence regions queued for VLM/human review
- `vlm_reviews`: append-only literal VLM readings linked to one persisted review candidate, including model identity and crop SHA-256
- `decisions`: accepted text, actor (automatic/VLM/human), timestamp, rationale/evidence refs
- `jobs`: stage, page scope, state, attempts, error, timing
- `exports`: format, verified revision, path/hash

Coordinates use a normalized page coordinate system in addition to engine-native geometry.

A decision never deletes engine evidence. Corrections create/replace the decision layer while source OCR remains immutable. A VLM reading is evidence, not automatically an accepted decision.

Operational job rows are intentionally mutable because they describe execution state rather than source evidence. Retrying a job updates its state, attempt count, error, and timestamps; it does not overwrite OCR, verification, VLM, or decision evidence.

## Implemented persistence slices

Schema version 1 persists `projects`, `pages`, `engine_runs`, `ocr_lines`, `ocr_tokens`, and append-only `decisions` with foreign-key protection. Recording another OCR pass creates a new engine run rather than updating old OCR rows. Decision history stores explicit evidence references and does not mutate source OCR text.

Schema version 2 adds append-only `verification_runs`, `alignments`, `alignment_conflicts`, `risks`, and `review_candidates`. Each verification run references the exact two immutable OCR engine runs that produced it. Recomputing alignment/risk creates a new verification run instead of overwriting the previous comparison. Version 1 databases migrate forward without deleting existing projects or OCR evidence.

Schema version 3 adds append-only `vlm_reviews`. Each row references an existing review candidate, records model/version, literal text, uncertainty, crop path, and the SHA-256 of the exact crop bytes supplied to the model. Re-running a VLM adds another evidence row instead of overwriting prior model output. Version 2 databases migrate forward without deleting verification evidence.

Schema version 4 adds resumable `jobs`. Jobs are uniquely scoped by project, stage, and optional page. `import` is project-scoped; OCR, verification, and VLM review stages are page-scoped. A job records `pending`, `running`, `failed`, or `succeeded` state, attempt count, last failure, and start/finish timestamps. `ensure_job` is idempotent for a given scope. Pending, failed, and interrupted-running jobs remain discoverable for resume; succeeded jobs are not restarted accidentally. Version 3 databases migrate forward without deleting existing evidence.

`exports` remains planned and will be added through a later versioned migration rather than a destructive database reset.
