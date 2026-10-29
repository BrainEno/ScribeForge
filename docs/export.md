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

- `VerifiedPage(page_index, lines)` — accepted lines in reading order, plus the
  layout evidence (`line_boxes`, `line_roles`) that turns them into paragraphs.
- `VerifiedBook(title, pages)` — pages in page order.

Export reads paragraphs, not lines: `VerifiedPage.blocks()` groups the accepted
lines with geometry and block labels, so a printed line break is not a paragraph
break. See [Paragraphs and structural fidelity](./paragraphs.md).

## Reading layout: pages become chapters, not documents

A scan is not a book. Reflowing an EPUB one XHTML file per scanned page produces a
file that opens, but every page turn cuts a sentence in two, the chapter tree is
only a table of contents pointing into a flat run of pages, and a reader cannot
style the text. Export therefore re-flows the verified text before rendering:

`scribeforge.domain.flow.build_flow(book, layout=...)` returns documents, each
holding an ordered list of paragraphs with the page they start on:

| layout | documents | use |
| --- | --- | --- |
| `chapter` (default when the book has a chapter tree) | one document per chapter, front matter first | the reading edition |
| `page` (default when there is no chapter tree) | one document per scanned page | a facsimile, for checking against the scans |

`--layout auto` picks the first two rules; `--layout page` and `--layout chapter`
force one (`--layout chapter` on a book with no chapter tree still yields one
document, titled from its first page). The facsimile layout is kept on purpose: it
is the only shape in which "page N of the EPUB" and "page N of the scan" are the
same thing, which is what side-by-side review and the paragraph fixtures compare
on.

### Paragraphs that run across a page turn

When a chapter becomes a single document, a paragraph can finally close across a
page boundary. Two independent signals have to agree before the lines are joined:

1. the last accepted line of the previous page **does not end a sentence**
   (`。！？…` or a closing quote, `scribeforge.domain.paragraphs.ends_sentence`), and
   it is not a short line (a centred heading or the last line of a block is short
   even without punctuation);
2. the first accepted line of the next page **is not indented** — an indented first
   line starts a new paragraph, whatever the previous page ended with.

Only `body` lines are joined: a `heading` or `footnote` block never continues onto
the next page. Pages must be adjacent in the same document, and a page with no
accepted lines breaks the chain, because there is no evidence of what came between.
When a store has no line geometry (a text-only engine, or an old store), only the
first signal is available and export takes it — the fallback is stated in
[Paragraphs](./paragraphs.md) rather than silently cutting every page.

Joining is the only text edit: a merged paragraph is the concatenation of the two
page's lines in order, never a rewrite. Every page of the book still appears
exactly once, in order, and the check below proves it.

On the six real transcriptions in `benchmark/corpus/v`, re-typeset at the book's
measure with the page break placed inside the page's longest paragraph, the rule
rejoined the paragraph on **6 / 6** pages, leaving the paragraph count and every
character unchanged (`pytest tests/test_flow.py -m benchmark --no-cov`). The text
and the layout are real; the position of the break is chosen by the test, because
no adjacent pair of pages has been transcribed by a person yet. A real book's
merge rate is therefore **not** measured, and this document does not claim one.

### Page breaks inside a document

Reflow must not lose the printed page. Every page in a document leaves a marker
before its first paragraph:

```html
<span epub:type="pagebreak" role="doc-pagebreak" id="page-42" title="117"/>
```

`id="page-42"` is the source page index (stable, and the same anchor the facsimile
layout uses), `title="117"` is the printed folio a reader would cite — the same
number `structure` derived from the scan. A reader jumps to a page, and the
`pagebreak` vocabulary lets accessibility tooling announce it.

Placement is honest but not perfect: markers are emitted as siblings, each one
before the paragraphs that *start* on its page. A paragraph joined across a turn is
one element emitted at the page it started on, so the marker of the page it ran onto
lands just after that paragraph instead of inside it. The alternative — cutting the
element in two around the marker — would reintroduce exactly the split the layout
exists to remove, and inlining the marker inside the paragraph would make the text
comparison below ambiguous. The cost is bounded: a jump to a page number can land a
few lines past the true page turn, never a different page.

DOCX is still written page by page: Word is the deliverable for comparing against
the scans, and its explicit page breaks already preserve the scan's pagination.
Chapter reflow there is a separate change, not a silent one.

## Repair before export

A pair that no rule could settle is not a dead end. `scribeforge repair` re-reads
it from a wider, enlarged crop and accepts a text only when at least two
independent readers agree; see
[Self-comparison and self-correction](./self-correction.md). Run it between
`process` and `export` on a book whose review queue is dominated by low-level
disagreements — `scribeforge export` itself still refuses to write a partial book,
and `repair` does not relax that gate.

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

- a page has no verification run (`unverified_pages`),
- a pair has no accepted decision (`unresolved_pairs`), or
- a page produced **no aligned pairs at all** and was never confirmed as textless
  (`unconfirmed_pages`).

Export then exits with code `2` and says exactly what is missing, with the blocker
code for each unresolved pair:

```
error: cannot export unresolved content:
  1 unresolved pair(s):
    page 0 pair 0: unrepresentable_text (U+FFFD)
    inspect with 'scribeforge review'
    decide with 'scribeforge resolve --page N --pair M --text ...'
```

There is no "export anyway" switch: silently dropping or substituting unverified
text would break the evidence-grade guarantee.

A page with zero aligned pairs is not evidence of a blank leaf — a crashed worker, a
mis-decoded raster and an empty leaf look identical in the OCR tables. The refusal
quotes the measured ink so the operator can tell the two apart:

```
  2 page(s) with no text at all, not yet confirmed as textless:
    page 7: measured ink 6.93% — there is content here that was not read
    page 12: measured ink 0.03% — nothing on the page
    if the page really has no text: 'scribeforge confirm --page N --kind blank --reason ...'
    if it is a plate or illustration with no caption to read: --kind non_text
```

A page measuring under 1% ink is confirmed textless by the measurement itself
(`actor = evidence`) during the structure pass, so genuinely blank leaves do not
need a person. Everything above that needs an explicit confirmation:

```
scribeforge confirm --store <dir> --page 3 --kind blank --reason "empty leaf before the plate"
```

`--kind non_text` covers a plate or illustration. Confirmations are append-only
(`page_confirmations`, schema v9) and recorded in the export report.

## Who accepted the text

"Verified" is not one thing. `VerifiedBook.acceptance` summarises how the exported
text was accepted:

| Field | Meaning |
| --- | --- |
| `pairs` / `pages` | accepted pairs and exported pages |
| `machine_accepted` | accepted by the pipeline (`automatic` or `vlm` decisions) |
| `human_accepted` | accepted by a person (`scribeforge resolve`) |
| `machine_acceptance` | share of accepted pairs a machine certified |
| `max_machine_risk` | highest `content_score` among machine-accepted pairs |
| `spot_check` | machine-accepted pairs worth a look: low confidence, unrepresentable characters, or risk at/above `spot_check_risk` |
| `requires_human_review` | `True` when `spot_check` is non-empty |

Default export writes the book and prints a warning when a spot check is due.
`--strict-acceptance` turns that warning into a refusal (exit code `2`), for
deliverables that must be human-certified. `--json` prints the whole summary
(`machine_accepted`, `human_accepted`, `max_machine_content_risk`,
`requires_human_review`, `spot_check[]`) next to the output path.

Export also reports the table of contents it wrote (`table of contents: N chapter(s)
from the book's structure`), and `--json` includes `chapters`. Chapter titles come
from the book's own contents page when it can be anchored, otherwise from headings
on the pages; see [Book structure](./book-structure.md).

See [The acceptance gate](./acceptance-gate.md) for the rule that decides what a
machine may accept at all.

## EPUB 3 mapping

`scribeforge.adapters.epub` writes a deterministic EPUB 3 archive with the
standard zip layout:

| Entry | Purpose |
| --- | --- |
| `mimetype` | `application/epub+zip`, first entry, stored uncompressed |
| `META-INF/container.xml` | points at `OEBPS/content.opf` |
| `OEBPS/content.opf` | EPUB 3 package: identifier, title, author, language, `dcterms:modified`, manifest, spine |
| `OEBPS/nav.xhtml` | EPUB 3 navigation document: the chapter tree when the book's structure was derived, one entry per page otherwise |
| `OEBPS/doc-000001.xhtml` … | chapter layout: one XHTML document per chapter, in spine order; one element per reconstructed paragraph (`<p>`, `<h2>`, `<aside epub:type="footnote">`) plus a page-break marker for every scanned page it covers |
| `OEBPS/page-000001.xhtml` … | facsimile layout (`--layout page`): one XHTML document per verified page, same element vocabulary |

Document file names follow the layout so a reader can tell at a glance which shape
it opened; the ids inside (`doc-0`, `page-42`) are stable across both. The default
is the chapter layout whenever the book has a chapter tree, so a 562-page scan
opens as a book of chapters rather than 562 files.

Metadata:

- identifier: `urn:scribeforge:<source_sha256>`
- title: the project title, or `--title`
- author: `--author`, written as `dc:creator` (omitted when absent)
- language: a BCP 47 tag, `und` by default
- `dcterms:modified`: an injected UTC timestamp, or `--modified` for reproducible builds

Text is XML-escaped, so source characters such as `<` and `&` survive unchanged.
Every document also points back to page evidence through its page-break markers,
so whatever the layout, a span of exported text can be traced to the scanned page
it came from.

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
| `word/document.xml` | one `<w:p>` per reconstructed paragraph (headings use `Heading2`, footnotes are superscript), page breaks between pages, trailing `<w:sectPr/>` |
| `docProps/core.xml` | title, author (`dc:creator`), identifier, language, `dcterms:modified` |

An accepted blank line separates paragraphs and is not emitted as an empty
paragraph; every paragraph keeps `xml:space="preserve"` so leading and trailing
spaces survive. Pages are separated by an explicit
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
  package, every manifest target exists, the spine matches the rendered document
  order, and each XHTML document reproduces its accepted blocks exactly — in
  order, element for element, with unknown elements treated as a failure;
- EPUB, page coverage: **every verified page appears exactly once, in source
  order**, across the whole spine — a page that vanished and a page that got
  emitted twice are both errors, whatever the layout;
- EPUB, page markers: the `page-N` ids collected from the rendered documents must
  be exactly the documents' pages in order, so a page break cannot be dropped or
  duplicated while a paragraph is re-flowed;
- DOCX: `[Content_Types].xml` is first, every part has a content type, every
  relationship target exists, page breaks match the page count, and each page
  reproduces its accepted blocks exactly;
- the package title equals the book title.

Whitespace is compared after squashing, because joining two lines can insert one
space between Latin characters; every other character difference is an error, so
a lost character can never pass the check.

A mismatch raises `ExportIntegrityError` (a `ValueError`), so `export` exits `2`
instead of writing a file whose text drifted from the accepted decisions.

## CLI

```bash
scribeforge export --store store/ --out book.epub
scribeforge export --store store/ --out book.docx
scribeforge export --store store/ --out book.epub --language zh
scribeforge export --store store/ --out book.epub \
  --title "书名" --author "作者" --modified 2026-10-20T00:00:00Z
scribeforge export --store store/ --out scans.epub --layout page   # facsimile
```

The format is chosen from the destination extension: `.epub` writes EPUB 3,
`.docx` writes WordprocessingML. Any other extension is rejected.

`--layout` accepts `auto` (default), `chapter`, or `page`, and only affects EPUB;
DOCX is always written page by page. The run summary says which shape was used and
how much text crossed a page turn:

```
exported 562 page(s) to book.epub
layout: chapter — 12 document(s), 1180 paragraph(s)
paragraphs continued across a page turn: 37
table of contents: 12 chapter(s) from the book's structure
```

`--json` adds `layout`, `documents`, and `paragraphs` beside the acceptance report.

`--modified` accepts an ISO 8601 timestamp (a trailing `Z` is accepted); naive
values are interpreted as UTC. Supplying it makes the build reproducible: the
same book and metadata produce byte-identical output.

Exit codes: `0` written, `2` unverified content, missing store/project, no pages,
unsupported extension, invalid metadata (empty title, unsafe language tag,
unparseable timestamp), or source text XML cannot represent.
