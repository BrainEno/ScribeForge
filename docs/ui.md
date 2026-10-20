# Desktop shell

The desktop shell (`scribeforge ui --store <dir>`) is a **read-only** view over an
existing project store. It is the first Phase 3 slice: it shows what the pipeline
already persisted and never edits it. Recording decisions from the UI, the
source/text synchronized viewer, the first-run runtime wizard, and settings come
later.

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

- `scribeforge.ui.viewmodel` holds every label and row the shell renders, plus
  the paths of the imagery to show (`page_image_path`, `load_pair_view`). It
  imports no GUI framework and is unit tested against a real `SQLiteStore`, so
  the presentation contract is verified without a display.
- `scribeforge.ui.tk_window` is the Tkinter widget layer. It is display-only code
  excluded from coverage (`.github/workflows/ci.yml` runs on a headless runner)
  and is smoke-tested by launching the app.
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
