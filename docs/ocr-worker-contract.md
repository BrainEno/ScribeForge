# OCR Worker Contract v2

Heavy OCR backends are isolated from the ScribeForge core. A worker is invoked with:

```text
<command> --image <path> --page-index <zero-based-index>
```

It writes exactly one UTF-8 JSON document to stdout. Diagnostic logging belongs on
stderr so stdout remains machine-readable.

## Example

```json
{
  "schema_version": 2,
  "engine": "mineru",
  "engine_version": "x.y",
  "page_index": 0,
  "direction": "horizontal-lr",
  "lines": [
    {
      "text": "source text",
      "box": [0.1, 0.2, 0.5, 0.04],
      "confidence": 0.93,
      "tokens": [
        {"text": "字", "confidence": 0.98, "box": [0.1, 0.2, 0.02, 0.04]}
      ]
    }
  ],
  "blocks": [
    {"type": "title", "box": [0.1, 0.2, 0.5, 0.04], "line_start": 0, "line_end": 1}
  ]
}
```

Boxes are normalized page coordinates `[x, y, width, height]` in 0..1. Geometry is
clamped into the page by the adapter: a polygon that spills past the trim or carries
negative coordinates after deskew is corrected rather than rejecting the page.

## Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `schema_version` | yes | `1` or `2`. New workers emit `2`. |
| `engine` | yes | Must equal the engine name the adapter was constructed with. |
| `engine_version` | yes | Free-form string, persisted as evidence. |
| `page_index` | yes | Must equal the requested zero-based page index. |
| `direction` | v2 | One of `horizontal-lr`, `horizontal-rl`, `vertical-rl`, `vertical-lr`. Defaults to `horizontal-lr`. |

`direction` is read as an **assertion about the page**, not a default: a worker
that omits the field (every v1 worker, and any v2 worker that genuinely cannot
tell) is recorded as *undeclared*, and ScribeForge then infers the direction from
line geometry. Omitting it is safer than guessing `horizontal-lr` — a guessed
horizontal declaration would suppress geometry detection and mis-pair every
page of a vertical book. Declaring is only worthwhile when the engine really
decides it (e.g. PaddleOCR-VL's layout stage); do not echo a constant.
| `lines[].text` | yes | Verbatim recognized text; never normalized by the worker. |
| `lines[].box` | yes | Normalized line geometry. |
| `lines[].confidence` | v2 | Optional line-level score in 0..1. |
| `lines[].tokens` | optional | Native token/word/character entries with their own geometry; `null` or omitted means unavailable, `[]` means the worker reported no token entries. |
| `lines[].tokens[].text` | yes, when a token exists | Verbatim recognized token text. |
| `lines[].tokens[].box` | yes, when a token exists | The token's own normalized geometry; never copy the enclosing line/block box as a token box. |
| `lines[].tokens[].confidence` | optional | Per-token score in 0..1, only when the engine reports one. |
| `blocks[]` | v2 | Structural regions covering a contiguous run of lines. |

### Blocks

`line_start` is inclusive, `line_end` is exclusive, and both index `lines`. A block
whose range covers no lines is not reported.

`type` is one of:

`text`, `title`, `footnote`, `figure`, `table`, `header`, `footer`, `formula`,
`marginalia`, `unknown`

Workers translate engine-specific labels into these values. Common aliases are mapped
centrally (`page_footnote` → `footnote`, `image` → `figure`, `paragraph` → `text`, …).
**An unrecognised label becomes `unknown` rather than failing the page**, because a new
engine category must not make previously readable pages unprocessable.

Blocks are evidence, not decoration. They drive three downstream behaviours:

- `header`/`footer` are page furniture and are excluded from the readable text layer.
- `figure`/`table` are non-text and are excluded from the text layer.
- `title` identifies chapter headings; `footnote` keeps notes out of the body flow.

### Confidence and token absence

Line and token confidence are independent evidence. Omitting a score means *unknown*,
which is materially different from a low score: unknown never raises the
low-confidence risk and never counts as agreement. A worker must not emit `0.0` as a
stand-in for "no score", or copy a line score into every token as if it were measured
per-token confidence. A known low line-level score is still preserved and can trigger
review even when token data is unavailable.

Token availability has three states: an omitted `tokens` field or `"tokens": null`
means the engine did not provide token evidence; `"tokens": []` means it explicitly
reported no token entries; a non-empty array carries token/word/character evidence.
These states survive adapter parsing and SQLite persistence. If an engine has no native
token/word geometry, it must report `null` rather than wrap a line or region in a fake
token. PaddleOCR's worker runs PP-OCRv4 and requests its native
[`return_word_box` output](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/OCR.en.md),
mapping PaddleX's serialized `text_word` / `text_word_boxes` fields (see the
[OCR result serializer](https://github.com/PaddlePaddle/PaddleX/blob/develop/paddlex/inference/pipelines/ocr/result.py)). PP-OCRv4's CTC path supports this output; current PP-OCRv5/v6 Transformers recognizers do not. The worker preserves `rec_score` at line level and does not fabricate per-word scores. MinerU middle-JSON and PaddleOCR-VL region output do not currently provide token boxes, so those workers report `null`.

## Compatibility

- The adapter accepts both `schema_version` 1 and 2.
- The missing / `null` / empty-array token semantics apply to both versions; they do
  not require workers to change their schema version.
- A v1 document has no blocks, so **every line counts as text**. Absence of block
  evidence must never silently delete content.
- Unknown block labels degrade to `unknown`, which is also retained as text.

## Why this boundary exists

Adapters reject schema, engine, and page identity mismatches. This allows MinerU,
PaddleOCR and future engines to live in independent environments without importing
their dependency stacks into the core process.
