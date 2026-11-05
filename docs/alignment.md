# Dual OCR Alignment

ScribeForge compares independent OCR evidence before any language-model adjudication.

The current domain alignment layer is deliberately engine-neutral. It accepts two `PageOCRResult` values for the same page and returns line pairs plus explicit conflicts.

## Current behavior

- Regions grouped by the existing main-axis centre heuristic are paired only if
  each is the other's unique nearest region, within 0.12 normalized units. A
  nearest-neighbour margin below 0.01 leaves the regions unmatched. This replaces
  ordinal `zip` pairing, which shifted all matches when a region was missing.
- Candidate lines must overlap on the main axis and be within the existing
  cross-axis distance threshold. These fixed, uncalibrated thresholds are
  conservative heuristics, not a proof of column identity.
- Explicit conflicting block types, explicit `UNKNOWN`, and overlapping
  incompatible block labels are not matched. If an engine supplies no block for
  a line, compatibility behavior remains unchanged: its role is **not** known.
  This is not a fully conservative solution for missing block evidence; the
  existing acceptance policy can still accept such a match.
- Unmatched lines retain original indexes, carry insertion/deletion conflicts,
  and enter the existing risk/review pipeline. A near-tie at the region stage is
  represented only as unmatched, not as a structured ambiguity record;
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

One-to-many line splits, merged lines, rotated text, fully structured ambiguity,
and missing block evidence remain unsupported. The line matcher is still greedy:
near-tied line candidates can be silently assigned. This partial mitigation does
not satisfy issue #79 or the group precision/recall acceptance criteria. Existing
1:1 pair indexes and decisions are not rewritten, and no schema migration is
introduced; future 1:N support needs an explicit schema and end-to-end review,
acceptance, repair and export design. The fixed thresholds require validation on
representative labeled scans before being treated as reliable layout rules.

For P and S lines, Cp and Cs groups, and B total block-to-line memberships,
column ranking costs O(Cp*Cs log Cs + Cp*Cp log Cp) worst case, block indexing
O(B), and candidate line examination O(P*S), with O(P*S) worst-case candidate
storage. Text comparison adds input-text-dependent SequenceMatcher costs. This
is bounded per page but is not a linear-time or large-scale performance claim.
Complex column hierarchies also need explicit
layout evidence rather than assuming that a direction-only sort is sufficient.
