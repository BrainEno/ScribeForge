# OCR Risk Scoring

ScribeForge risk scoring is evidence-based. It must not use language-model fluency as a correctness signal because literary text may intentionally contain rare syntax, fragments, punctuation, or unusual wording.

The current page risk layer consumes a dual-engine `PageAlignment` plus both immutable OCR results and produces deterministic per-pair risk assessments.

Signals in the first version:

- substantive text disagreement;
- punctuation-only disagreement;
- missing or extra lines;
- low OCR line or token confidence;
- Unicode replacement/control anomalies;
- suspicious vertical mismatch between aligned lines.

Scores are capped at 1.0. A configurable threshold converts risky pairs into `ReviewCandidate` records. Each candidate retains the page index, alignment pair index, risk reasons, score, and a normalized crop box that unions the source geometry from both OCR engines.

## Two scores, two purposes

Each `PairRisk` carries the total `score` — the review queue ranks by it — and a
narrower `content_score` that counts only *evidence quality* signals:

| Signal | `score` | `content_score` |
| --- | --- | --- |
| substantive text disagreement | 0.45 | — |
| variant-only disagreement | 0.10 | — |
| punctuation disagreement | 0.20 | — |
| missing/extra line | 0.55 | — |
| low line/token confidence (< 0.80) | 0.25 | 0.25 |
| Unicode anomaly | 0.35 | 0.35 |
| vertical spatial mismatch (> 0.04) | 0.15 | 0.15 |

The default crop-candidate threshold is `0.25`, matching the low-confidence signal,
so a line below the confidence floor receives a review crop even when both engines
agree. Callers may explicitly request a stricter threshold. The human review queue
still lists every undecided pair, independent of candidate filtering.

`PairRisk.min_confidence` records the lower of the two readers' confidences (`None`
when neither reports one). Both `content_score` and `min_confidence` exist because
the [acceptance gate](./acceptance-gate.md) judges *evidence quality*, not
*structural disagreement*: a disagreement a vision model already settled is a closed
question, and scoring it into the acceptance decision would block every VLM
resolution.

The score does not choose which engine is correct. It only decides what evidence deserves additional VLM or human review.
