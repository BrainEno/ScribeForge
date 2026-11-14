# Multi-engine adjudication (issue #90)

ScribeForge's core asset is the *disagreement → risk → review → decision*
skeleton, not any single OCR model. `adjudicate_readings` in
`src/scribeforge/domain/decisions.py` is the single source of truth for the
decision rule, and it is **N-ary**: the two-engine `adjudicate_pair` is a thin
wrapper over it, so introducing a third engine does not require a new rule —
only new engine texts.

## The rule (pure function, no I/O)

Inputs: `engine_texts: Sequence[str | None]`, an optional `VLMReading`, an
optional `PairRisk` and the acceptance policy. Output: `PairAdjudication` with
`selected_text`, `actor`, `rationale`, `blockers` and a `votes` summary.

1. **No engine text at all** → unresolved (`no_engine_text`).
2. **Agreement cluster**: texts are clustered by exact equality or
   glyph-variant equivalence (`is_variant_only_difference`). A cluster held
   by **at least two engines** wins and its *first literal text* is kept
   verbatim — source OCR text is never folded or "corrected".
   - every produced text in one cluster, all identical → `engines_agree`
   - every produced text in one cluster, variant-only differences →
     `variant_agreement`
   - a proper subset cluster wins → `majority_agreement` (2-of-3 with three
     engines; degenerates to the historical two-engine behavior)
3. **Confident VLM corroboration**: a non-uncertain, non-blank VLM reading
   matching *any* engine text (exact or variant-only) resolves the pair as
   the `vlm` actor (`vlm_confident`). A VLM reading matching nothing is a
   third opinion, not corroboration (`vlm_uncorroborated`); uncertain/blank
   VLM never resolves.
4. **Everything else** — including three-way disagreement — stays queued for
   a human (`engines_disagree`, `missing_engine_text`, `vlm_*`).

Whatever a rule accepts still passes the acceptance gate (`machine_blockers`)
unchanged: agreement answers "do independent readers agree?", the gate answers
"is this text fit to accept without a person?".

## Vote summaries

`summarize_engine_votes(engine_texts)` returns an `EngineVoteSummary`
(`engine_count`, `text_count`, `cluster_sizes`, `winner_size`, `unanimous`,
`majority`, `split`). Missing engine readings count toward `engine_count` but
form no cluster. Every `PairAdjudication` carries its `votes`, so reports can
show the split/majority/unanimous distribution of a whole book.

## Exhaustive testing

`tests/test_adjudication_truth_table.py` enumerates the full product space of
a 4-symbol alphabet (exact-agree, variant pair, disagree, missing) across 3
and 4 engines × 7 VLM states (448 + 1792 cells) and checks every cell against
an independent expectation model written from this spec — including the
three-way-disagreement case staying queued.

## Named optional backends

The technology-route backends are first-class by name (issue #90):

- `scribeforge setup --json --backend paddleocr-vl|surya` repairs exactly that
  backend (same on-demand install path as mineru/paddleocr; both stay out of
  the default install because of multi-GB weights).
- `scribeforge runtime-status` (Markdown and `--json`) reports all four
  backends with an `optional` flag, state and failed step; the final
  `setup --json` record includes them too.
- `build_runtime_plan` plans all four; each has health checks probing the
  right module (`PaddleOCRVL` for paddleocr-vl, `surya.layout` for surya) and
  a committed hash-locked requirements file.

## Licensing note

Surya's model weights are **OpenRAIL-M**, not Apache-2.0 (code is
Apache-2.0) — see `docs/licenses.md`. PaddleOCR-VL is Apache-2.0. Any
distribution bundling the weights must label them separately.

## Wiring a third engine into the pipeline

1. Add a worker module under `src/scribeforge/workers/` implementing the
   worker contract v2 (`docs/ocr-worker-contract.md`) and register it in the
   backend→worker map in `cli.py`.
2. Add an optional `RuntimeBackendPlan` in `runtime/planner.py` with a
   hash-locked requirements file under `runtime/locks/` and a health check
   importing the backend's entry module. Keep `optional=True` so default
   installs stay lean.
3. Feed its page text into `adjudicate_readings([primary, secondary,
   tertiary], vlm, ...)` — no rule change needed; the exhaustive truth table
   already covers the 3-engine space.
4. Record the engine's license (code vs. weights separately) in
   `docs/licenses.md` before any release bundles it.

## What is deliberately *not* done

- No pairwise 2-of-3 voting between engine combinations: clusters are
  computed over the full text set once, which is equivalent but simpler and
  deterministic (first literal of the winning cluster wins ties).
- No VLM override of engine agreement: a confident VLM reading only resolves
  pairs the engines *could not* agree on; it never rewrites an agreed text.
