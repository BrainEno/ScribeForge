# Review Crop Planning

VLM review is scoped to uncertain evidence regions, not entire book pages by default.

The crop planner converts a normalized `ReviewCandidate.crop` into a deterministic integer pixel box using the source page dimensions. A small configurable page-relative padding is added for visual context and clamped to page edges. Left/top coordinates are rounded outward with `floor`, while right/bottom coordinates use `ceil`, so conversion never clips visible candidate pixels through rounding.

Each crop receives a stable name such as:

```text
page-000008-pair-0003.png
```

This layer plans geometry only. Image decoding/encoding will be implemented behind a separate adapter so the domain remains independent of Pillow, OpenCV, PDF renderers, or GUI frameworks.
