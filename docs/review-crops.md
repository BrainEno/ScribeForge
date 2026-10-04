# Review Crop Planning and Materialization

VLM review is scoped to uncertain evidence regions, not entire book pages by default.

The crop planner converts a normalized `ReviewCandidate.crop` into a deterministic integer pixel box using the source page dimensions. A small configurable page-relative padding is added for visual context and clamped to page edges. Left/top coordinates are rounded outward with `floor`, while right/bottom coordinates use `ceil`, so conversion never clips visible candidate pixels through rounding.

Each crop receives a stable name such as:

```text
page-000008-pair-0003.png
```

## Image adapter

`scribeforge.adapters.image_crops.materialize_review_crops` is the image I/O boundary. The domain layer remains independent of Pillow, OpenCV, PDF renderers and GUI frameworks.

The adapter:

- decodes the prepared page image with Pillow;
- validates every planned crop against the actual decoded page dimensions;
- rejects mixed-page batches, duplicate pair indexes, duplicate output names and path traversal in file names;
- writes the exact planned pixel region as PNG;
- writes through a temporary file and atomically replaces the stable destination so an interrupted write does not leave a partially encoded crop;
- replaces a stale crop when the current page evidence changes rather than silently reusing old pixels;
- never modifies the source page image.

The returned mapping is keyed by alignment pair index and can be passed directly to the resumable VLM review orchestration. The later SQLite evidence step hashes the exact generated crop bytes before storing the VLM reading.
