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

No concrete VLM runtime is claimed as supported by this slice. A model backend will be added only after pinned local-runtime contract tests are available.
