# V..pdf: review and measurement plan

The reported Windows trial (9 pages, no page-level failures, 233 open review
items, roughly 2.4 pages/minute) is useful operational evidence, not an OCR
accuracy measurement. The review count alone cannot distinguish engine errors,
alignment errors, page furniture and genuinely difficult layout. The 562-page
book may also contain harder material than the first nine pages.

## Review findings and priorities

* Each page calls two independent OCR subprocesses in sequence; each worker
  starts a backend CLI. Startup is a plausible cost, but is not yet measured
  separately from inference. A persistent worker would require new cancellation,
  timeout, process-tree cleanup and crash/retry guarantees; do not assume it is
  faster or safe without testing those contracts.
* CLI automatic page concurrency is based on CPU count, with no GPU budget by
  default. The desktop now requests serial processing as a conservative starting
  point, not as a throughput improvement. Even one page can exceed available RAM
  or VRAM. CLI users can explicitly experiment with concurrency.
* Preprocessing changes the OCR coordinate frame and may damage fine strokes;
  compare independently created projects, not changed settings in a resumed one.
  Do not make binarization the default for old scans based on visual appearance.
* The reference corpus at `benchmark/corpus/v/` samples varied layout and has
  single-pass transcription, not double-proofread gold. Include human review of
  omissions and reading order, including agreement errors between both engines.
* EPUB round-trip checks establish consistency with recorded decisions, not
  correctness of the scan transcription. The normal export gate and source
  evidence must remain intact; candidate exports are explicitly unverified.

## Windows validation protocol

1. Record source SHA-256, app revision, model/runtime versions, GPU driver,
   device placement, RAM/VRAM, preprocessing settings, and whether models were
   downloaded or cached. Preserve the existing project; do not overwrite it.
2. Use the same representative pages and independent fresh projects for each
   configuration. Include the supplied corpus pages and representative figures,
   footnotes, dense and sparse pages from the actual book. Record any sampling or
   PDF-splitting method and physical page indices. Do not publish the book text.
3. Measure end-to-end wall clock externally, including startup, and report page
   count, failures/retries, peak RAM and GPU VRAM, and pages/minute. Capture
   `scribeforge report --store <store> --json` and, where appropriate,
   `process --events-file <path>`; report stage spans are only latest attempts
   at whole-second resolution, not complete wall time. Compare cold and warm
   runs separately and repeat. Events can contain project information; review
   before sharing.
4. Start with `--workers 1`; only test `--workers 2` on a separate fresh project
   after measuring single-page peaks and reserving substantial headroom. If
   using `--gpu-memory-mib`, understand it is a scheduling estimate, not a GPU
   memory limit. Stop on OOM or instability. Never extrapolate throughput
   linearly from a fake-engine benchmark.
5. Compare review reasons and page-level queue size, then manually compare both
   OCR outputs and accepted decisions against the scan, including shared errors,
   missed content, punctuation, footnotes, figures and order. Compute CER only
   against reviewed ground truth with a documented normalization policy; report
   layout/omission errors separately. A smaller queue does not prove accuracy.
6. After resolving review items and blank-page confirmations, run the normal EPUB
   export and validate its structure and reading order against the source. Record
   unresolved image/layout limitations separately. Do not call a candidate EPUB
   a verified book.

The above is an experiment plan, not a claim that the optimization or the full
Windows GUI workflow has been validated. The present development environment has
no installed OCR models or GPU; real model results must come from the target host.
