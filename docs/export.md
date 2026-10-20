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
| `OEBPS/content.opf` | EPUB 3 package: identifier, title, language, `dcterms:modified`, manifest, spine |
| `OEBPS/nav.xhtml` | EPUB 3 navigation document listing every page |
| `OEBPS/page-000001.xhtml` … | one XHTML document per verified page, `<p>` per accepted line |

Metadata:

- identifier: `urn:scribeforge:<source_sha256>`
- title: the project title
- language: a BCP 47 tag, `und` by default
- `dcterms:modified`: injected UTC timestamp

Text is XML-escaped, so source characters such as `<` and `&` survive unchanged.
Page documents are named from the source page index, which keeps the mapping back
to page evidence stable.

## Determinism

The zip uses a fixed DOS epoch for every entry and a fixed entry order, and the
only time-dependent field is the injected `dcterms:modified`. Two builds with the
same book, identifier, language, and timestamp are byte-identical.

`write_epub` writes through a temporary file and `Path.replace`, so an interrupted
export never leaves a torn EPUB at the destination.

## CLI

```bash
scribeforge export --store store/ --out book.epub
scribeforge export --store store/ --out book.epub --language zh
```

Exit codes: `0` written, `2` unverified content, missing store/project, no pages,
or invalid metadata.
