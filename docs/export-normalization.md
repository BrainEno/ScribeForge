# Export-only text normalization (issue #88)

ScribeForge has two deliberately separate kinds of normalization:

- `domain/normalize.py` creates conservative **comparison keys**. It helps alignment
  and adjudication recognize glyph-only disagreements; its folded text is never an
  export candidate.
- `domain/export_normalization.py` creates an explicit **derived reading copy** at
  the final export boundary. It never edits OCR results, verification decisions, or
  the accepted `VerifiedBook` assembled from SQLite.

## Policies

```bash
# Default: literal accepted text, zero changes.
scribeforge export --store store/ --out book.epub

# Convert only the explicitly curated compatibility variants.
scribeforge export --store store/ --out book.epub \
  --normalization canonical_variants

# Real OpenCC Traditional-to-Simplified conversion (t2s).
scribeforge export --store store/ --out book.docx \
  --normalization simplified
```

| Policy | Exported text | Converter |
| --- | --- | --- |
| `preserve` | Exactly the accepted text | none |
| `canonical_variants` | Explicitly listed variants such as `𠮷→吉`, `髙→高`, `峯→峰`, `羣→群` | `canonical_variants:v1` |
| `simplified` | OpenCC character-level traditional-to-simplified output | `opencc:t2s` |

The canonical table is intentionally small and reviewed. Missing variants remain
unchanged rather than being guessed. It is not a table of ancient/modern semantic
equivalents and must not be used for collation. OpenCC `t2s` is not a scholarly
traditional-to-simplified editorial decision, does not perform Taiwan/Hong Kong
vocabulary conversion, and does not establish OCR accuracy.

Accepted lines, chapter titles, and figure captions are derived together so the
`VerifiedBook` image/caption invariant still holds. Book title and author metadata
are not implicitly converted. Page images and all upstream evidence bytes remain
unchanged.

## Change report

Every verified export writes an adjacent report:

```text
book.epub
book.epub.normalization.json
```

This includes `preserve`, whose report proves `change_count: 0`. Re-exporting the
same output path replaces both the derived output and report, so a previous
non-default policy cannot leave a stale description beside a preserved export.

The versioned JSON report contains:

- `policy` and `converter`;
- `source_sha256`: the canonical text-model digest before derivation;
- `normalized_sha256`: the same digest after derivation;
- `output.file` and `output.sha256`: the exact EPUB/DOCX bytes beside the report;
- `change_count` and every change's location, page/line/chapter index, source and
  normalized offsets, original text, replacement text, and rule identifier.

Offsets are zero-based Python/Unicode code-point offsets in the individual accepted
line, chapter title, or figure caption. `source_end`/`normalized_end` are exclusive.
A one-character canonical mapping has one record per character. OpenCC works on a
whole line so phrase conversion remains possible; non-equal spans are recorded with
`SequenceMatcher(autojunk=False)` rather than pretending every phrase conversion is
an independent one-character rule.

The report is evidence about the derived output, not a new editorial decision and
not a replacement for the source scan or decision history.

## Package metadata and integrity checks

- EPUB 3 writes `<meta property="scribeforge:normalization">POLICY</meta>` in
  `OEBPS/content.opf`.
- DOCX writes `ScribeForge normalization: POLICY` in `dc:description` under
  `docProps/core.xml`.

Exporters validate the policy name. They receive the derived book and run the same
archive re-read checks against that book, so an export cannot claim one policy while
its body silently contains another text layer. `export --json` includes the policy,
converter, text digests, change count, output SHA-256, and report path without
printing a potentially book-sized change list to stdout.

## Dependency and scope

`opencc>=1.4.2,<2` is an Apache License 2.0 dependency. The pure domain module takes
a conversion callable and does not import OpenCC; `adapters/opencc_text.py` owns that
integration. No GPL dependency is introduced.

This slice does not add Flutter controls, automatic normalization during OCR or
adjudication, editorial translation, glyph-variant completeness claims, or a new
accuracy benchmark. The CLI is explicit opt-in except for the zero-change report;
`preserve` remains the default reading and evidence contract.
