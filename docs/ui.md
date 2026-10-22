# Desktop shell

The desktop shell (`scribeforge ui --store <dir>`) is an interactive human-review
surface over an existing project store. It can append an accepted decision for a
queued pair, but it never edits source OCR, engine output, alignment, VLM
readings, or page images. Decisions remain a separate, append-only evidence layer.

## Layout

| Pane | Shows |
| --- | --- |
| Library | one row per project: title, page count, queued pairs, failed job count |
| Jobs | every persisted job in creation order, e.g. `primary_ocr [page 0] failed attempts=1 error=engine crashed` |
| Review queue | one row per pair that still lacks an accepted decision: page, pair, risk score, risk reasons |
| Pair view | three tabs for the selected pair: `Evidence`, `Page image`, `Crop` |

The `Evidence` tab shows both engine readings, the newest VLM reading (with its
uncertainty flag), the materialized crop path when one exists, and the currently
accepted text. The `Page image` and `Crop` tabs show the immutable page raster
asset and the review crop for that pair, each with its path and displayed size in
the caption; a missing asset, a missing crop, or an undecodable file is stated in
the caption rather than hidden.

The `Compare` tab shows the primary and secondary OCR lines side by side.
Stored character conflict ranges are highlighted by type: replacement, insertion,
deletion, and punctuation. An empty side of an insertion/deletion uses a visible
empty-set marker, and a line missing from one engine is labelled as unaligned. The
comparison uses persisted alignment offsets rather than running a new diff in
the UI, so it does not silently change the evidence being reviewed.

Selecting a queued pair now moves the `Page image` tab to the source page and
draws a display-only outline around the persisted candidate region. The pair region
is orange; `Primary line [a]` focuses the primary OCR line in blue; `Secondary
line [d]` focuses the secondary OCR line in green. `Pair region [p]` restores
the wider region. The focus controls are disabled when the relevant geometry is
unavailable. Highlighting is drawn on a transient preview only: source image files
and stored OCR geometry are not changed.

The decision controls can explicitly accept the primary reading (`1`), secondary
reading (`2`), or VLM reading (`3`), or save literal custom text with
`Ctrl+Enter`. Empty custom text requires confirmation because it records a
deletion-like reading. The VLM button is available only when a VLM reading exists;
uncertain readings are still shown and may be selected deliberately by the human.
After a decision, the library counts and queue reload from SQLite, and the next
remaining queued pair is selected. `n` advances to the next queued pair and
`r`/`F5` refreshes the view. The source-focus shortcuts are `p`, `a`, and
`d`; review and focus shortcuts do not intercept text entered in the custom field.

Selecting a library row loads that project's jobs and queue; selecting a queue
row loads the pair's evidence and imagery. `r` and `F5` reload the library from
the store.

## Image previews

`scribeforge.adapters.image_preview` is the viewer's image I/O boundary:
`load_preview(path, box=..., upscale=...)` opens a file, applies the EXIF
orientation, converts to RGB for display, and fits it into a box. Page images are
only ever shrunk; crops may be enlarged so small regions stay readable. Missing
files raise `FileNotFoundError` and undecodable bytes raise `ValueError`, and the
shell reports either in the caption.

Previews are display-only. They are never written back, never cached into the
store, and never feed the OCR, verification, decision, or export layers, so the
source evidence stays untouched.

## Layering

- `scribeforge.ui.viewmodel` holds labels, evidence rows, image paths, and the
  tested `resolve_review_row` application action. It imports no GUI framework
  and is unit tested against a real `SQLiteStore`, so the decision contract is
  verified without a display.
- `scribeforge.ui.tk_window` owns the Tkinter widgets and keyboard bindings.
  Its presentation layer remains excluded from coverage because CI is headless;
  the decision/persistence behavior is covered through the view-model tests.
- `scribeforge.ui.app` resolves the Tkinter layer lazily, so every other command
  keeps working on Python builds without Tkinter and on machines with no display.
  A missing Tkinter reports `error: the desktop UI needs Tkinter (...)` and exits
  `2`.

Tkinter is used deliberately: it ships with CPython, so the project adds no
dependency and CI stays fast. Because all presentation logic lives in the
view-model, replacing the widget layer later does not touch tested logic.

## Evidence rules

The shell reads only what the CLI reads: `SQLiteStore`, `review_queue`, and
`review_detail`. It never rewrites source OCR, never invents text, and never
hides an unresolved pair — a project with queued pairs shows them in the queue
and in the library row count.

## Exit codes

`scribeforge ui` exits `0` when the window closes normally and `2` when the store
is missing or the interpreter has no Tkinter.
