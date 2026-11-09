# Dual OCR Alignment

ScribeForge compares independent OCR evidence before any language-model adjudication.
Alignment never corrects or rewrites OCR text. It records bounded relationships between
original OCR lines, explicit character conflicts, unmatched evidence, and unresolved
mapping ambiguity.

## Direction and layout evidence

The matcher accepts two `PageOCRResult` values for the same page. It retains the
existing direction-aware behavior for `horizontal-lr`, `horizontal-rl`, `vertical-rl`,
and `vertical-lr`: cross-axis distance is Y for horizontal pages and X for vertical
pages, and final output is sorted with `domain.reading_order`.

The current matcher uses all of the following as constraints rather than treating text
similarity as sufficient evidence:

- normalized line and group bounding boxes;
- page direction and cross-axis distance;
- physical X-column geometry;
- declared `OCRBlock` roles;
- reading order within each column;
- adjacency of lines inside a proposed group;
- glyph-folded text similarity (folding is for scoring only).

Physical columns are clustered by X for both horizontal and vertical writing. Column
assignment is a monotonic geometric DP with hard X-overlap/centre gates, not `zip()` of
two column lists. Geometrically unrelated columns cannot pair. If competing column
assignments are within `0.05`, their connected region becomes an explicit
`COLUMN_MAPPING` ambiguity with the candidate index mappings and geometric scores;
it is not guessed by list position or flattened into unrelated unmatched lines.
Within paired columns, horizontal and vertical lines are both ordered
top-to-bottom; final groups use the page's declared/resolved direction, including
right-to-left vertical column order.

### Block-role rules

Declared roles are evidence, not labels to infer away:

- a group may not contain two different roles on one engine side;
- `TITLE`, `TEXT`, `HEADER`, `FOOTER`, and `FOOTNOTE` regions do not pair across
  different declared roles;
- same-role adjacent lines may form a group even when an engine emitted separate
  blocks;
- `UNKNOWN` and missing block evidence are never promoted to `TEXT` or another guessed
  role. Two wholly unknown sides may form a multi-line candidate using the normal hard
  geometric gates. If exactly one side has known role evidence, a multi-line candidate
  is allowed only when that side is uniformly `TEXT`; known titles, headers, footers,
  footnotes and mixed known/unknown groups do not receive this relaxation. This keeps
  ordinary PP-OCR (which has no layout labels) compatible with MinerU body-line splits
  without treating missing evidence as a verified block type.

Consequently a title cannot be silently absorbed into a body group, and a footnote
cannot be paired with nearby body text merely because the strings are similar. Two
unlabelled regions with inadequate geometry remain unmatched or ambiguous. The code
does not claim semantic block classification that the engines did not provide.

## Bounded group candidates

`align_pages(..., max_group_size=3)` supports 1:1, 1:2, 2:1 and larger contiguous
combinations such as 2:2 and 3:2. `K` may be set from 1 through **4**; values outside
that range are rejected. Unmatched transitions always consume exactly one line. This
is a hard bound against combinatorial growth, not whole-page concatenation.

For a candidate at reading-order positions `(i, j)`, the DP may consume `p = 0..K`
primary lines and `s = 0..K` secondary lines. A matched candidate requires `p >= 1`
and `s >= 1`; insertion/deletion transitions are `(1,0)` and `(0,1)`. Lines must be
contiguous in their paired column and adjacent in Y (gap no greater than the cross-axis
threshold). A candidate is rejected before text comparison when:

1. block roles are incompatible;
2. union-box cross-axis distance exceeds `max_cross_axis_distance` (default `0.08`);
3. main-axis interval gap exceeds `0.05`;
4. a multi-line candidate's joined comparison similarity is below `0.70`.

Only the small candidate group's strings are assembled for comparison. Every member
retains its original engine-local line index, and the immutable source OCR lines remain
separate in storage.

### Source-derived group text

`domain.text_assembly.assemble_group_text()` is the single assembly rule used by group
scoring/conflicts, risk and review text, adjudication, repair-round readings and final
accepted-text assembly. It never edits `OCRLine.text` or uses the folded comparison key
as export text. Existing whitespace is preserved; CJK boundaries receive no invented
separator; Latin word/letter-digit boundaries receive one space unless existing
whitespace, a hyphen or an explicit punctuation boundary already joins them. Purely
numeric runs join without guessing an internal thousands/word separator. This is a
bounded OCR-line assembly rule, not paragraph reflow or hyphenation correction.

`fold_for_comparison()` preserves whitespace rather than deleting it. Consequently
`New York` versus `NewYork`, multiple versus single spaces, and CJK strings that differ
only by spaces cannot become `variant_agreement` and machine-accept silently. Legitimate
OCR line-break differences are handled by assembly before glyph/punctuation folding;
traditional/variant-character comparison behavior is unchanged. An uncertain Latin
word-internal split (for example `inter` + `national` versus `international`) remains a
text conflict instead of being normalized away.

## Score and constrained two-best DP

For matched group similarity `S`, spatial score `G = 1 - distance / threshold`, and
`B = p + s - 2` additional line boundaries, the transition score is:

```text
4*S + 0.5*G - 0.25*B + 2.25*S*B
```

The last term is a text-coverage/consolidation reward. Without it, two mediocre 1:1
matches receive the spatial base twice and can outrank one exact 2:2 partition of the
same evidence. Separate exact 1:1 matches still outrank an unnecessary merged group.
An unmatched line scores `-1.5`; geometry remains a hard gate regardless of score.

Each column is solved with dynamic programming over `(i, j)`, keeping two backpointers
per state. This finds the best complete constrained path instead of committing pairs
by global best-first greedy order. The old greedy approach could not represent group
membership, could strand lines after a locally strong choice, and had no principled
way to compare a 1:2 path with two 1:1 matches plus an unmatched line.

A narrow exception handles OCR boxes reported in opposite order: two adjacent 1:1
matches may be swapped only when both pairs of source boxes overlap in Y, both crossed
candidates pass all geometry/role gates, and the crossed total score improves by more
than the ambiguity margin. It does not permit arbitrary permutation of a page.

### Near-tie ambiguity

The DP also reconstructs the runner-up complete path. If its score is within
`ambiguity_margin` (default `0.20`) of the best path, the differing region is not
silently committed. Common unaffected groups are retained; affected original lines
become one `AMBIGUOUS` `LineAlignment` containing:

- the complete affected primary and secondary index tuples;
- `AmbiguityReason.CLOSE_SCORES` and the complete-path score margin;
- at least two `AlignmentAlternative` records, each with both original index tuples,
  text similarity, cross-axis distance, and transition score.

Near-equal physical column candidates use `AmbiguityReason.COLUMN_MAPPING` and the same
alternative/member records. Their scores are the geometric column scores, and the
ambiguous region is the union of the connected competing columns. Every alternative
must remain inside that region; a missing membership row therefore fails closed during
loading rather than silently narrowing what a reviewer decided.

`RiskReason.ALIGNMENT_AMBIGUITY` gives the region a review candidate. Adjudication and
repair rounds refuse to machine-accept it even if the strings in one alternative agree;
a fresh reading cannot prove which geometric mapping is correct. A person can still
append a cited human decision. CLI review JSON exposes status, all member indexes, and
all alternatives. Flutter parses these additive fields and displays alignment status,
0-based original indexes, 1:N/N:1 membership, and scored candidate mappings. Legacy JSON
without the fields displays unknown/missing structure rather than inventing indexes from
the queue pair index.

## Output order and conflicts

Matched, unmatched, and ambiguous groups are sorted by the union of all their source
boxes with the existing direction-aware `reading_order_key`; original indexes provide
stable tie-breaking. Repeated runs therefore produce the same dataclass values for the
same input. Horizontal output retains the established top-to-bottom then column-side
ordering rather than inventing a new multi-block reading-order model.

Character conflicts are computed between the bounded group's two comparison strings.
Exact grouped evidence has no conflict. A primary-only line is `deletion`, a
secondary-only line is `insertion`, replacements are `substitution`, and
punctuation-only replacements are `punctuation`. Conflict ranges refer to the bounded
comparison strings; the member tuples are required to recover the original lines.

## Complexity and deterministic limits

For a paired column with `n` primary lines, `m` secondary lines, and cap `K`, there are
`O(n*m)` states and at most `(K+1)^2 - 1` transitions per state. Two entries per state
make time `O(n*m*K^2)` and DP space `O(n*m)`, plus `O((n+m)*K)` precomputed group
profiles and the cost of comparing the actual character strings. Candidate objects are
not retained in a separate full transition cache. Column pairing adds
`O(Cp*Cs)` time and space; line sorting adds `O(L log L)`. `K <= 4` bounds the
combinatorial factor. A deterministic 120-line regression checks source-index coverage
and guards against accidental full-grid text comparison or path copying.

The two-best method intentionally exposes close alternatives; it does not enumerate an
exponential set of all possible alignments.

## Schema v15 and historical decisions

The old `alignments` row, `pair_index`, scalar `primary_line_index` /
`secondary_line_index`, and `vertical_distance` columns are retained. For a new group,
each scalar is its first member; for every migrated old pair it remains exactly the old
single member. No old pair is recomputed or reordered during migration.

Schema v15 adds:

- `alignment_line_members`: ordered original indexes for every side of every pair;
- `alignment_ambiguities`: reason and complete-path margin;
- `alignment_alternatives`: scores for each uncommitted mapping;
- `alignment_alternative_members`: ordered original indexes for each alternative.

Upgrading v14 only backfills one member row for each non-NULL old scalar. It does not
infer ambiguity, change pair indexes, move decisions, or attribute uncertain history.
Loading a supposedly multi-line record with missing or inconsistent membership rows
fails closed. Recording a verification validates source-run bounds, rejects reuse of an
OCR line in two pairs, and requires every alternative to stay inside its ambiguous
region. Decisions continue to cite the verification pair and immutable engine runs;
the persisted membership rows resolve that citation to all source lines. Existing
rules that refuse to apply decisions to another verification run are unchanged.

## Ground-truth fixtures and measured scope

`tests/fixtures/alignment_groundtruth.json` contains deterministic, hand-labelled
synthetic pages for horizontal splits/merges, repeated text in two columns, vertical RL
and LR, title/header/body/footnote roles, duplicate positions, extra/missing lines, and
close-score ambiguity. The evaluation itself lives in production code:
`src/scribeforge/domain/alignment_eval.py` (`AlignmentSample` / `evaluate_alignment_samples`
/ `render_alignment_report`) runs each sample through the real `align_pages` plus the
risk and review stages and compares **groups of original line indexes**, never page-wide
concatenated text, reporting group precision, recall, F1, reading-order errors,
cross-column errors, unresolved ambiguities, 1:N / N:1 counts and review-queue entries.
`tests/test_alignment_multiline.py` drives the fixtures through that module. All explicit
mappings in the deterministic fixtures must be found exactly. The ambiguity fixture
must expose both alternatives instead of contributing a forced match.

`tests/test_worker_alignment_contract.py` additionally passes raw MinerU middle JSON and
Paddle recognition/PP-Structure-shaped JSON through the actual worker extraction
functions, contract-v2 JSON serialization and the production adapter. It covers both
1:N and N:1 with Paddle layout evidence and with the plain default PP-OCR path, plus
ambiguous/no layout ownership. `tests/test_text_assembly.py` covers English splits in
both directions, different break positions, mixed CJK/Latin text, traditional Chinese,
Japanese, automatic acceptance and exported spaces.

These are algorithm and wire-contract regression fixtures, not real OCR accuracy
measurements. No OCR model is downloaded or run by these tests; they say nothing about
recognition accuracy on representative scans.

## Remaining limits

- Rotated text is covered by a fail-safe regression
  (`rotated-text-is-not-force-matched` in the fixtures): a 90-degree-rotated line
  whose box is geometrically incompatible with its twin is **never force-matched**
  on text equality — the whole page's uncertain mappings stay unmatched and enter
  the review queue. Arbitrary nested layout trees still require explicit upstream
  layout evidence; this change does not add a layout classifier.
- Asymmetric non-body or mixed-role block metadata deliberately prevents multi-line
  merging, which can leave genuine title/footnote splits for review. Unknown evidence is
  never promoted to a declared role.
- Only the best two line paths and close geometric column alternatives are examined;
  higher-order ambiguity handling remains bounded rather than enumerating every
  imaginable alternative.
- Group assembly infers only the documented line-boundary separators, not paragraph
  semantics or whether every Latin split is word-internal. Human/editorial text remains
  a separate decision layer.
- Synthetic fixtures prove the stated rules and evidence integrity, not arbitrary scan
  quality or real-engine accuracy. Real representative scans remain necessary before
  claiming production OCR accuracy.
