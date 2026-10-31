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
- `decisions`: accepted text, actor (automatic/VLM/human), timestamp, rationale/evidence refs, and the risk score/reasons that were in force when it was accepted
- `page_confirmations`: append-only statements that a page has no text (blank leaf or plate), with actor, reason, optional ink ratio
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

Job execution uses a nonblocking project-scoped OS writer lock, stored outside the project assets and keyed by the resolved SQLite path plus project ID. Only the store that acquired it may complete or fail its active job. A competing process gets a retryable ownership error without incrementing attempts or changing evidence. The OS releases the lock on process termination; the next process can then reclaim a persisted `running` job, incrementing (not resetting) its attempt history. This needs no schema migration and works with local filesystem locks; do not assume advisory locks are coordinated by every network filesystem.

Schema version 5 rebuilds `jobs` to widen the stage constraint, version 6 adds
`page_structure`, `chapters`, `ocr_blocks` and the line-level confidence/direction
columns, and version 7 records the direction an alignment was computed in.

Schema version 8 adds `engine_runs.scope`. `NULL` means the run read the whole
page; `pair:<n>` means it read one aligned pair from a region crop during a repair
round. Region runs are evidence in their own right (they can support an accepted
decision) but are deliberately invisible to `latest_engine_run`, so a one-line
re-read can never masquerade as the newest reading of a page.

Schema version 9 adds what the [acceptance gate](./acceptance-gate.md) reads and
what it must be able to explain later:

- `risks.min_confidence` — the lower of the two readers' confidences (`NULL` when
  neither reported one), and `risks.content_score` — the part of the risk score that
  comes from evidence quality rather than structural disagreement. Persisting both is
  not optional: a gate that recomputes from an in-memory structure reads
  "no risk" after a reload and silently stops blocking.
- `decisions.risk_score` / `decisions.risk_reasons_json` — the risk a decision was
  accepted under. Policies change; a decision must keep the basis it was judged on.
- `page_confirmations` — append-only confirmation that a page carries no text
  (`kind` in `blank`/`non_text`, `actor` in `human`/`evidence`, free-text `reason`,
  optional `ink_ratio`). Two engines returning nothing is also what a crash looks
  like, so this fact needs a named source.

Version 8 databases migrate forward to 9 without deleting any project, OCR,
verification, or decision evidence.

Schema version 10 stores the evidence behind a page's role and the provenance of a
chapter title:

- `page_structure.ink_ratio` — measured share of non-background pixels. Textless
  pages carry no text whether they are blank leaves or plates, and only the raster
  says which (see [Book structure](./book-structure.md)).
- `chapters.source` — `toc` (the book's own contents page, anchored to a physical
  page through the printed label) or `heading` (one page's typography), or `manual`.
  The two are not equally strong evidence, and an export that mixes them says which
  is which.

Version 9 databases migrate forward to 10 without deleting any evidence.

`exports` remains planned and will be added through a later versioned migration rather than a destructive database reset.

**Schema v11 records the page raster's identity.** `pages` gains four nullable
columns — `page_asset_sha256`, `page_asset_byte_size`,
`page_asset_parameters_json`, `page_asset_version` — populated at import time from
the rendered page's bytes and render parameters (DPI, renderer, source format).
They are nullable so projects imported before v11 keep opening; a NULL digest means
"evidence from before v11" and is reported by the audit, not treated as an error.
`scribeforge rehash-pages` fills the gap for those pages without overwriting an
existing digest, so a changed page can never launder itself through a rehash.

The import pipeline writes these columns for every page it materializes, the
orchestrator verifies the source raster against them before any engine reads it,
the audit reports missing / mismatched / unreadable page assets, and `scribeforge
export` re-checks the digest before a cover or plate is embedded. The bytes and
their SHA-256 also travel on `VerifiedImage` (in memory only), and the export
check re-derives the digest from the archive entry it wrote: the image a reader
sees is provably the image that was imported, measured, and audited.

One consequence of the chapter layout is worth stating here because it changes an
old assumption: **a document in the exported EPUB is no longer a page.** One
XHTML file now holds a whole chapter, so text can no longer be attributed to a
single page by looking at which file it is in. Nothing in the database changes —
`VerifiedPage.lines` and `page_structure` stay per page — and the exported document
keeps a `pagebreak` marker per page, so the mapping back to evidence survives.
Anything that wants to infer "page N = file N" must use `--layout page`, where that
identity holds by construction. The raw `export-candidates` path is unaffected: it
is a separate writer (`scribeforge.adapters.candidate_epub`) that still emits one
spine item per source page, which is what the Flutter comparator aligns on. See
[Export](./export.md).

## Decision resolution

The decision layer needs no migration: it writes schema-v1 `decisions` rows through `scribeforge.pipeline.adjudication`. `adjudicate_pair` applies one deterministic rule — independent engine agreement is accepted as `automatic`, a confident non-blank VLM reading is accepted as `vlm` **only when it corroborates one of the engines**, and every other pair stays unresolved for human review. A model that disagrees with both engines is a third claim, not a second vote (issue #76).

Each recorded decision cites the verification run, both OCR engine runs, and the VLM reading when one was used.

Unresolved pairs can be re-read instead of only queued: `scribeforge.pipeline.rounds`
adds region-scoped engine runs (`engine_runs.scope = "pair:<n>"`) and accepts a text
when the readings reach consensus — at least two independent readers, strictly more
than any rival group. Those decisions cite the fresh runs and use the same
`automatic` / `vlm` actors, so the decision layer stays one append-only table.

Re-running adjudication is idempotent because a pair that already has a decision is left untouched; source OCR is never rewritten.

A human resolves a pair by appending a `decisions` row with `actor = human` (`record_human_decision`, exposed as `scribeforge resolve`). It cites the same evidence and, being newest, becomes the pair's latest decision while earlier rows are preserved, so a human can correct an `automatic` or `vlm` acceptance without mutating any engine, verification, or VLM evidence row.

`adjudicate_pair` additionally passes every automatic path through the
[acceptance gate](./acceptance-gate.md): agreement no longer suffices when the
accepted text is unrepresentable (`U+FFFD`, control characters), when the worst
reader admits low confidence, or when the pair's content risk exceeds the policy
ceiling. Blocked pairs are recorded as unresolved and stay in `review_queue`, which
lists **every undecided pair** rather than only score-thresholded candidates — a
queue built from candidates could report "0 queued" while export stayed blocked.

Export reads only this decision layer: `assemble_verified_content` takes the newest decision per pair and blocks when any page or pair is unresolved, so the reader output is always derived from accepted, cited evidence rather than raw OCR. It also blocks a page that produced zero aligned pairs until `page_confirmations` records why (`scribeforge confirm`), because "no pairs" is equally the signature of a failed read.
