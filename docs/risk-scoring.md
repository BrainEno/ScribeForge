# OCR Risk Scoring

ScribeForge risk scoring is evidence-based. It must not use language-model fluency as a correctness signal because literary text may intentionally contain rare syntax, fragments, punctuation, or unusual wording.

The current page risk layer consumes a dual-engine `PageAlignment` plus both immutable OCR results and produces deterministic per-pair risk assessments.

Signals in the first version:

- substantive text disagreement;
- punctuation-only disagreement;
- missing or extra lines;
- low OCR token confidence;
- Unicode replacement/control anomalies;
- suspicious vertical mismatch between aligned lines.

Scores are capped at 1.0. A configurable threshold converts risky pairs into `ReviewCandidate` records. Each candidate retains the page index, alignment pair index, risk reasons, score, and a normalized crop box that unions the source geometry from both OCR engines.

The score does not choose which engine is correct. It only decides what evidence deserves additional VLM or human review.
