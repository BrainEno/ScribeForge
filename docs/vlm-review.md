# VLM Review Boundary

The VLM stage is evidence gathering, not editing. It receives only review-candidate image crops created from OCR disagreement/risk and returns an additional literal reading.

## Literal transcription policy

Every worker must be instructed to:

- transcribe visible text only;
- preserve punctuation and line breaks exactly as visible;
- never correct grammar, spelling, syntax, wording, or style;
- never infer missing text from context or complete a sentence;
- mark genuinely ambiguous readings as uncertain.

A VLM reading does not automatically become verified text. It remains evidence until a later deterministic or human decision records the accepted reading.

## Worker contract v1

The core invokes an isolated local worker with an image crop, page index, pair index, and the literal-transcription policy. Stdout must contain one UTF-8 JSON document with:

```json
{
  "schema_version": 1,
  "model": "local-model-id",
  "model_version": "version",
  "page_index": 4,
  "pair_index": 2,
  "text": "literal visible transcription",
  "uncertain": false
}
```

The adapter rejects schema/model/page/pair mismatches. Missing crop evidence is an error: ScribeForge must not silently fall back to sending the full book page merely because a crop was not prepared.

## Resumable review orchestration

`scribeforge.pipeline.vlm_review.review_pending_candidates` is the restartable orchestration boundary for a single page. It asks a `VLMReviewState` for pair indexes that already have durable evidence, skips those pairs, and processes only the remaining review candidates.

Each successful reading is appended to the state immediately before the next crop is sent to the model. If a later crop fails, a retry can resume from the first unfinished pair instead of rerunning successful VLM work. Missing crops are checked only for pending pairs, so already-persisted evidence does not require recreating temporary crop files merely to resume.

`SQLiteVLMReviewState` is the durable implementation. Before appending a reading it hashes the exact crop bytes with SHA-256, then stores model identity, literal text, uncertainty, crop path and crop digest in schema-v3 `vlm_reviews`. Re-running a model appends new evidence instead of replacing an earlier reading. Completed-pair lookup is derived from persisted VLM evidence, not process memory.

A VLM review row is still not an accepted text decision. A later deterministic rule or human reviewer must explicitly create the decision layer from cited evidence.

No concrete VLM runtime is claimed as supported by this slice. A model backend will be added only after pinned local-runtime contract tests are available.
