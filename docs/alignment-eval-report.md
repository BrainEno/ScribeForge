# Alignment evaluation

Group-level comparison of predicted vs hand-labelled line-index groups; undetermined regions count as ambiguities and their review-queue entries are reported separately.

| Sample | TP | FP | FN | Precision | Recall | F1 | order_errors | cross_column | ambiguities | 1:N | N:1 | review_queue |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| horizontal-one-to-two | 2 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1 | 0 | 0 |
| horizontal-two-to-one | 2 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0 | 1 | 0 |
| horizontal-two-column-repeated-text | 4 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 |
| vertical-rl-split-and-merge | 2 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1 | 1 | 0 |
| vertical-lr-reading-order | 2 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 1 | 1 | 0 |
| header-title-body-footnote-regions | 4 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 |
| identical-text-at-different-positions | 2 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0 | 0 | 0 |
| primary-extra-line | 1 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0 | 0 | 1 |
| secondary-extra-line | 1 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0 | 0 | 1 |
| close-score-alternatives-are-ambiguous | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1 | 0 | 0 | 1 |
| rotated-text-is-not-force-matched | 0 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 0 | 0 | 0 | 4 |
| aggregate | 20 | 0 | 0 | 1.0000 | 1.0000 | 1.0000 | 0 | 0 | 1 | 3 | 3 | 7 |
