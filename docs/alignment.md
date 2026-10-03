# Dual OCR Alignment

ScribeForge compares independent OCR evidence before any language-model adjudication.

The current domain alignment layer is deliberately engine-neutral. It accepts two `PageOCRResult` values for the same page and returns line pairs plus explicit conflicts.

## Current behavior

- line candidates must be spatially near each other on the normalized page;
- nearby candidates are selected using text similarity, with vertical distance as a tie-breaker;
- exact matches produce no conflicts;
- replacements are classified as `substitution`, except punctuation-only replacements which are `punctuation`;
- text present only in the secondary engine is `insertion`;
- text present only in the primary engine is `deletion`;
- every conflict keeps character ranges in both source lines, while every line pair keeps the original line indexes.

No text is corrected or rewritten by alignment. It only records disagreement between OCR sources.

## Limitations of this first slice

The matcher is currently greedy and line-level. It does not yet solve multi-column reading order, one-to-many line splits, merged lines, rotated text, or block-level layout conflicts. Those cases will be added under tests before the risk/VLM stage depends on them.
