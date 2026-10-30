# Dual OCR Alignment

ScribeForge compares independent OCR evidence before any language-model adjudication.

The current domain alignment layer is deliberately engine-neutral. It accepts two `PageOCRResult` values for the same page and returns line pairs plus explicit conflicts.

## Current behavior

- line candidates must be spatially near each other on the normalized page;
- every primary/secondary line pair within range is scored once, then assigned
  globally best-first (highest text similarity, cross-axis distance as
  tie-breaker: Y for horizontal pages, X for vertical pages). Unlike a sequential
  greedy pass, this avoids an earlier line permanently claiming a secondary line
  that is a stronger match for a later one and stranding the later line with a
  weak match or false conflict;
- exact matches produce no conflicts;
- replacements are classified as `substitution`, except punctuation-only replacements which are `punctuation`;
- text present only in the secondary engine is `insertion`;
- text present only in the primary engine is `deletion`;
- every conflict keeps character ranges in both source lines, while every line pair keeps the original line indexes.

No text is corrected or rewritten by alignment. It only records disagreement between OCR sources.

## Limitations of this first slice

The matcher is still one-to-one and line-level: each primary line maps to at
most one secondary line (or none). Direction-aware matching and reading order
are separate concerns: `domain.reading_order` sorts line indexes for the four
page directions, and `domain.paragraphs` joins vertical columns without adding
spaces. These rules are covered by unit tests and the three-page real-scan Oracle
regression; that does not validate OCR model output or arbitrary multi-block layouts.

One-to-many line splits, merged lines, rotated text, and block-level layout
conflicts remain unsupported. Complex column hierarchies also need explicit
layout evidence rather than assuming that a direction-only sort is sufficient.
