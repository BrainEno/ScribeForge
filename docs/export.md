# Export

Export turns the **accepted decision layer** into a reading file. It never reads
raw OCR candidates, so a reader can never receive text that no automatic, VLM, or
human decision accepted.

## Source of truth

`scribeforge.pipeline.assembly.assemble_verified_content` walks the persisted pages
in order and, for each aligned pair, takes `SQLiteStore.latest_decision`. The
newest decision wins, so a human correction automatically supersedes an earlier
`automatic` or `vlm` acceptance without deleting the older evidence row.

The assembled model is deliberately minimal:

- `VerifiedPage(page_index, lines)` — accepted lines in reading order.
- `VerifiedBook(title, pages)` — pages in page order.

## Raw OCR candidate EPUBs

`scribeforge export-candidates --store <dir> --out-dir <dir>` creates separate
EPUB candidates from each requested engine's latest persisted raw OCR run for
every imported page. The defaults are `mineru` and `paddleocr`; use repeatable
`--engine NAME` arguments for another engine name stored in a test or custom
pipeline. A candidate is labelled **UNVERIFIED OCR CANDIDATE** in EPUB metadata,
uses one XHTML spine item per original source page, and includes an adjacent
`candidate-manifest.json` with source SHA-256, engine versions, per-page run IDs
and exported EPUB hashes. All requested candidates must be buildable before any
candidate file is written. Missing engine output on any page is a fatal error:
the command never silently produces a shorter book.

These files deliberately contain raw model output, not the accepted decision
layer. They are separate from `scribeforge export`, which continues to block
until the complete book has been verified and accepted.

## Partial books are blocked

Assembly refuses to produce a partial book. It raises `UnresolvedContentError`
when:

- a page has no verification run (`unverified_pages`), or
- a pair has no accepted decision (`unresolved_pairs`).

Export then exits with code `2` and points the operator at `scribeforge review`.
There is no "export anyway" switch: silently dropping or substituting unverified
text would break the evidence-grade guarantee.

## EPUB 3 mapping

`scribeforge.adapters.epub` writes a deterministic EPUB 3 archive with the
standard zip layout:

| Entry | Purpose |
| --- | --- |
| `mimetype` | `application/epub+zip`, first entry, stored uncompressed |
| `META-INF/container.xml` | points at `OEBPS/content.opf` |
| `OEBPS/content.opf` | EPUB 3 package: identifier, title, author, language, `dcterms:modified`, manifest, spine |
| `OEBPS/nav.xhtml` | EPUB 3 navigation document listing every page |
| `OEBPS/page-000001.xhtml` … | one XHTML document per verified page, `<p>` per accepted line |

Metadata:

- identifier: `urn:scribeforge:<source_sha256>`
- title: the project title, or `--title`
- author: `--author`, written as `dc:creator` (omitted when absent)
- language: a BCP 47 tag, `und` by default
- `dcterms:modified`: an injected UTC timestamp, or `--modified` for reproducible builds

Text is XML-escaped, so source characters such as `<` and `&` survive unchanged.
Page documents are named from the source page index, which keeps the mapping back
to page evidence stable.

XML 1.0 cannot represent most C0 control characters or lone surrogates. Both
exporters reject such a value (`ValueError` naming the page and line) instead of
writing an archive no reader can open; nothing is silently stripped or replaced.

## DOCX mapping

`scribeforge.adapters.docx` writes a deterministic WordprocessingML package with
the minimal OPC parts:

| Entry | Purpose |
| --- | --- |
| `[Content_Types].xml` | first part; declares the main-document and core-properties content types |
| `_rels/.rels` | package relationships to `word/document.xml` and `docProps/core.xml` |
| `word/document.xml` | one `<w:p>` per accepted line, page breaks between pages, trailing `<w:sectPr/>` |
| `docProps/core.xml` | title, author (`dc:creator`), identifier, language, `dcterms:modified` |

An accepted empty line becomes `<w:p/>`; text keeps `xml:space="preserve"` so
leading and trailing spaces survive. Pages are separated by an explicit
`<w:br w:type="page"/>` paragraph, so even a blank page occupies its own page.
Values XML cannot represent are rejected for the same reason as in EPUB.

Determinism (fixed zip timestamps and entry order) and atomic replacement are
shared with EPUB through `scribeforge.adapters.export_common`.

## Determinism

The zip uses a fixed DOS epoch for every entry and a fixed entry order, and the
only time-dependent field is the injected `dcterms:modified`. Two builds with the
same book, title, author, identifier, language, and timestamp are byte-identical;
`--modified` makes that timestamp explicit.

Both writers go through a temporary file and `Path.replace`, so an interrupted
export never leaves a torn file at the destination.

## Self-check

`scribeforge.adapters.export_check` re-reads the rendered bytes before they are
returned, and both builders run it automatically:

- the archive opens, every entry passes its CRC check, and every XML part parses;
- EPUB: `mimetype` is first and stored, the container points at an existing
  package, every manifest target exists, the spine matches the verified page
  order, and each XHTML page reproduces its accepted lines exactly;
- DOCX: `[Content_Types].xml` is first, every part has a content type, every
  relationship target exists, page breaks match the page count, and each page
  reproduces its accepted lines exactly;
- the package title equals the book title.

A mismatch raises `ExportIntegrityError` (a `ValueError`), so `export` exits `2`
instead of writing a file whose text drifted from the accepted decisions.

## CLI

```bash
scribeforge export --store store/ --out book.epub
scribeforge export --store store/ --out book.docx
scribeforge export --store store/ --out book.epub --language zh
scribeforge export --store store/ --out book.epub \
  --title "书名" --author "作者" --modified 2026-10-20T00:00:00Z
```

The format is chosen from the destination extension: `.epub` writes EPUB 3,
`.docx` writes WordprocessingML. Any other extension is rejected.

`--modified` accepts an ISO 8601 timestamp (a trailing `Z` is accepted); naive
values are interpreted as UTC. Supplying it makes the build reproducible: the
same book and metadata produce byte-identical output.

Exit codes: `0` written, `2` unverified content, missing store/project, no pages,
unsupported extension, invalid metadata (empty title, unsafe language tag,
unparseable timestamp), or source text XML cannot represent.
