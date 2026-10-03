# OCR Worker Contract v1

Heavy OCR backends are isolated from the ScribeForge core. A worker is invoked with:

```text
<command> --image <path> --page-index <zero-based-index>
```

It writes exactly one UTF-8 JSON document to stdout:

```json
{
  "schema_version": 1,
  "engine": "mineru",
  "engine_version": "x.y",
  "page_index": 0,
  "lines": [
    {
      "text": "source text",
      "box": [0.1, 0.2, 0.5, 0.04],
      "tokens": [
        {"text": "字", "confidence": 0.98, "box": [0.1, 0.2, 0.02, 0.04]}
      ]
    }
  ]
}
```

Boxes are normalized page coordinates `[x, y, width, height]` in 0..1. Diagnostic logging belongs on stderr so stdout remains machine-readable.

The adapter rejects schema, engine, and page identity mismatches. This boundary allows MinerU and PaddleOCR to live in independent environments without importing their dependency stacks into the core process.
