# Review Crop Planning

VLM review is scoped to uncertain evidence regions, not entire book pages by default.

The crop planner converts a normalized `ReviewCandidate.crop` into a deterministic integer pixel box using the source page dimensions. A small configurable page-relative padding is added for visual context and clamped to page edges. Left/top coordinates are rounded outward with `floor`, while right/bottom coordinates use `ceil`, with floating-point boundary noise removed so exact pixel edges do not accidentally grow by one pixel.

Each crop receives a stable name such as:

```text
page-000008-pair-0003.png
```

## Image adapter

`PillowCropWriter` is the first image adapter. It opens one already-rendered page image, writes only the requested review crops, validates that crop geometry stays inside the source page, and atomically replaces each PNG from a temporary file in the destination directory. Running the same crop plan again is therefore deterministic and does not leave partial output behind after a successful write.

The domain layer remains independent of Pillow, PDF renderers, model runtimes, and GUI frameworks. Full-book PDFs are still expected to be rendered/page-processed incrementally rather than decoded into memory as one document.
