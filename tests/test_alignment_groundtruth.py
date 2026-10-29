"""#79 复杂版面对齐：真值样本验收。

在标注的版面样本（双栏、竖排、脚注、断行）上报告匹配准确率与
顺序错误率，并确保不可确定的对齐结果进入人工复核。

每个样本都给出 primary / secondary 两个引擎的 OCR 行（含框坐标）
与期望的配对映射，验证：

1. 匹配准确率：期望配对的 primary 行都被正确配对；
2. 顺序错误率：返回的 pairs 按阅读顺序排列，未与期望顺序反转；
3. 复核入队：相似度低或结构差异大的样本进入 review_candidates。
"""

from __future__ import annotations

from scribeforge.domain.alignment import ConflictKind, align_pages
from scribeforge.domain.ocr import (
    BoundingBox,
    OCRLine,
    OCRToken,
    PageDirection,
    PageOCRResult,
)
from scribeforge.domain.risk import assess_page, review_candidates


def _result(
    engine: str,
    lines: list[tuple[str, float, float, float, float]],
    *,
    direction: PageDirection = PageDirection.HORIZONTAL_LR,
    declared: bool = False,
) -> PageOCRResult:
    """Build a page OCR result from (text, x, y, w, h) tuples."""

    return PageOCRResult(
        page_index=0,
        engine=engine,
        engine_version="1",
        lines=tuple(
            OCRLine(
                text,
                BoundingBox(x, y, w, h),
                (OCRToken(text, 0.9, BoundingBox(x, y, w, h)),),
            )
            for text, x, y, w, h in lines
        ),
        direction=direction,
        direction_declared=declared,
    )


def _match_rate(
    result_pairs, expected: dict[int, int | None]
) -> tuple[int, int, list[int]]:
    """Return (matched, total, mismatched_primary_indexes)."""

    matched = 0
    mismatched: list[int] = []
    for pair in result_pairs:
        p = pair.primary_line_index
        if p is None:
            continue
        expected_s = expected.get(p)
        actual_s = pair.secondary_line_index
        if expected_s is None and actual_s is None or expected_s is not None and actual_s == expected_s:
            matched += 1
        else:
            mismatched.append(p)
    return matched, len(expected), mismatched


# ------------------------------------------------------------------ two column


def test_groundtruth_two_column_matching_accuracy() -> None:
    """Two-column page: left column lines pair with left, right with right.

    Accuracy: 4/4 expected pairs correct, 0 order errors.
    """

    primary = _result(
        "mineru",
        [
            ("左栏第一行", 0.10, 0.20, 0.40, 0.05),
            ("右栏第一行", 0.60, 0.20, 0.40, 0.05),
            ("左栏第二行", 0.10, 0.35, 0.40, 0.05),
            ("右栏第二行", 0.60, 0.35, 0.40, 0.05),
        ],
    )
    secondary = _result(
        "paddleocr",
        [
            ("左栏第一行", 0.10, 0.21, 0.40, 0.05),
            ("右栏第一行", 0.60, 0.21, 0.40, 0.05),
            ("左栏第二行", 0.10, 0.36, 0.40, 0.05),
            ("右栏第二行", 0.60, 0.36, 0.40, 0.05),
        ],
    )

    alignment = align_pages(primary, secondary)

    expected = {0: 0, 1: 1, 2: 2, 3: 3}
    matched, total, _mismatched = _match_rate(alignment.pairs, expected)

    assert matched == total == 4, f"matched {matched}/{total}, mismatched {_mismatched}"
    # Order: reading order is top-to-bottom, left-to-right.
    order = [
        (p.primary_line_index, p.secondary_line_index) for p in alignment.pairs
    ]
    assert order == [(0, 0), (1, 1), (2, 2), (3, 3)], order
    # No conflicts: identical text.
    for pair in alignment.pairs:
        assert pair.conflicts == ()
        assert pair.text_similarity > 0.95


# ------------------------------------------------------------------ vertical


def test_groundtruth_vertical_rl_matching_accuracy() -> None:
    """Vertical right-to-left page: rightmost column read first.

    Accuracy: 6/6 expected pairs correct. Column order (right->left) is
    preserved; within a column, top-to-bottom is preserved.
    """

    primary = _result(
        "mineru",
        [
            # Right column (read first), top to bottom.
            ("右列第一字", 0.70, 0.20, 0.05, 0.40),
            ("右列第二字", 0.70, 0.35, 0.05, 0.40),
            ("右列第三字", 0.70, 0.50, 0.05, 0.40),
            # Left column (read second), top to bottom.
            ("左列第一字", 0.30, 0.20, 0.05, 0.40),
            ("左列第二字", 0.30, 0.35, 0.05, 0.40),
            ("左列第三字", 0.30, 0.50, 0.05, 0.40),
        ],
        direction=PageDirection.VERTICAL_RL,
        declared=True,
    )
    secondary = _result(
        "paddleocr",
        [
            ("右列第一字", 0.70, 0.21, 0.05, 0.40),
            ("右列第二字", 0.70, 0.36, 0.05, 0.40),
            ("右列第三字", 0.70, 0.51, 0.05, 0.40),
            ("左列第一字", 0.30, 0.21, 0.05, 0.40),
            ("左列第二字", 0.30, 0.36, 0.05, 0.40),
            ("左列第三字", 0.30, 0.51, 0.05, 0.40),
        ],
        direction=PageDirection.VERTICAL_RL,
        declared=True,
    )

    alignment = align_pages(primary, secondary)

    expected = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    matched, total, _mismatched = _match_rate(alignment.pairs, expected)

    assert matched == total == 6, f"matched {matched}/{total}, mismatched {_mismatched}"
    # Reading order: right column first (top->bottom), then left column.
    order = [(p.primary_line_index, p.secondary_line_index) for p in alignment.pairs]
    assert order == [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5)], order
    for pair in alignment.pairs:
        assert pair.conflicts == ()
        assert pair.text_similarity > 0.95


# ------------------------------------------------------------------ footnote


def test_groundtruth_footnote_matching_accuracy() -> None:
    """Page with body text plus a footnote at the bottom margin.

    Accuracy: 5/5 expected pairs correct. The footnote line pairs with the
    footnote line, not with a body line.
    """

    primary = _result(
        "mineru",
        [
            ("正文第一行内容", 0.10, 0.20, 0.80, 0.05),
            ("正文第二行内容", 0.10, 0.30, 0.80, 0.05),
            ("正文第三行内容", 0.10, 0.40, 0.80, 0.05),
            ("脚注：这里是脚注内容", 0.10, 0.85, 0.80, 0.05),
            ("页码 123", 0.45, 0.95, 0.10, 0.03),
        ],
    )
    secondary = _result(
        "paddleocr",
        [
            ("正文第一行内容", 0.10, 0.21, 0.80, 0.05),
            ("正文第二行内容", 0.10, 0.31, 0.80, 0.05),
            ("正文第三行内容", 0.10, 0.41, 0.80, 0.05),
            ("脚注：这里是脚注内容", 0.10, 0.86, 0.80, 0.05),
            ("页码 123", 0.45, 0.96, 0.10, 0.03),
        ],
    )

    alignment = align_pages(primary, secondary)

    expected = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}
    matched, total, _mismatched = _match_rate(alignment.pairs, expected)

    assert matched == total == 5, f"matched {matched}/{total}, mismatched {_mismatched}"
    order = [(p.primary_line_index, p.secondary_line_index) for p in alignment.pairs]
    assert order == [(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)], order
    for pair in alignment.pairs:
        assert pair.conflicts == ()


# ------------------------------------------------------------------ line break


def test_groundtruth_line_break_matching_accuracy() -> None:
    """A sentence split across two lines; engines break at different points.

    One-to-one alignment cannot merge fragments across different break points,
    so the boundary mismatch is classified as a conflict (not silently merged)
    and the pair enters review. This is the #79 limitation being documented.
    """

    primary = _result(
        "mineru",
        [
            ("他望着那只乌", 0.10, 0.20, 0.40, 0.05),
            ("鸦。", 0.10, 0.30, 0.10, 0.05),
        ],
    )
    secondary = _result(
        "paddleocr",
        [
            ("他望着那", 0.10, 0.21, 0.35, 0.05),
            ("只乌鸦。", 0.10, 0.31, 0.35, 0.05),
        ],
    )

    alignment = align_pages(primary, secondary)

    # Both lines appear in output (none silently dropped); each pair carries
    # a classified conflict at the break boundary.
    assert len(alignment.pairs) == 3  # deletion + insertion + matched-with-conflict
    all_conflicts = alignment.pairs[0].conflicts + alignment.pairs[1].conflicts + alignment.pairs[2].conflicts
    kinds = {c.kind for c in all_conflicts}
    assert ConflictKind.DELETION in kinds
    assert ConflictKind.INSERTION in kinds

    # The pair enters review because the break mismatch raises the risk score.
    from scribeforge.domain.risk import assess_page, review_candidates
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary)
    assert len(candidates) >= 1, "break mismatch must enter review queue"


# ------------------------------------------------------------------ uncertain -> review


def test_groundtruth_uncertain_alignment_enters_review() -> None:
    """Two engines disagree substantially; the pair must enter review queue.

    This guards the #79 acceptance criterion: "不可确定的对齐结果进入
    人工复核" — low-confidence / high-disagreement pairs are not silently
    accepted as automatic.
    """

    primary = _result(
        "mineru",
        [("完全不同的文本内容", 0.10, 0.20, 0.80, 0.05)],
    )
    secondary = _result(
        "paddleocr",
        [("totally different text content", 0.10, 0.21, 0.80, 0.05)],
    )

    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.pair_index == 0
    # High disagreement -> high score -> above the default 0.35 threshold.
    assert candidate.score >= 0.35, candidate.score
    assert any(
        r is not None for r in candidate.reasons
    ), "candidate must carry risk reasons"


def test_groundtruth_report_accuracy_and_order_error_rates() -> None:
    """Aggregate report: accuracy and order-error rates across all samples.

    This is the #79 acceptance artifact: a single test that reports both rates
    so they can be tracked over time.
    """

    samples: list[tuple[PageOCRResult, PageOCRResult, dict[int, int | None]]] = [
        (
            _result(
                "mineru",
                [
                    ("左栏第一行", 0.10, 0.20, 0.40, 0.05),
                    ("右栏第一行", 0.60, 0.20, 0.40, 0.05),
                    ("左栏第二行", 0.10, 0.35, 0.40, 0.05),
                    ("右栏第二行", 0.60, 0.35, 0.40, 0.05),
                ],
            ),
            _result(
                "paddleocr",
                [
                    ("左栏第一行", 0.10, 0.21, 0.40, 0.05),
                    ("右栏第一行", 0.60, 0.21, 0.40, 0.05),
                    ("左栏第二行", 0.10, 0.36, 0.40, 0.05),
                    ("右栏第二行", 0.60, 0.36, 0.40, 0.05),
                ],
            ),
            {0: 0, 1: 1, 2: 2, 3: 3},
        ),
        (
            _result(
                "mineru",
                [
                    ("右列第一字", 0.70, 0.20, 0.05, 0.40),
                    ("右列第二字", 0.70, 0.35, 0.05, 0.40),
                    ("左列第一字", 0.30, 0.20, 0.05, 0.40),
                    ("左列第二字", 0.30, 0.35, 0.05, 0.40),
                ],
                direction=PageDirection.VERTICAL_RL,
                declared=True,
            ),
            _result(
                "paddleocr",
                [
                    ("右列第一字", 0.70, 0.21, 0.05, 0.40),
                    ("右列第二字", 0.70, 0.36, 0.05, 0.40),
                    ("左列第一字", 0.30, 0.21, 0.05, 0.40),
                    ("左列第二字", 0.30, 0.36, 0.05, 0.40),
                ],
                direction=PageDirection.VERTICAL_RL,
                declared=True,
            ),
            {0: 0, 1: 1, 2: 2, 3: 3},
        ),
    ]

    total_pairs = 0
    correct_pairs = 0
    order_errors = 0

    for primary, secondary, expected in samples:
        alignment = align_pages(primary, secondary)
        matched, total, _mismatched = _match_rate(alignment.pairs, expected)
        total_pairs += total
        correct_pairs += matched

        # Order error: a pair whose secondary index is out of reading order
        # relative to the previous pair.
        prev_s: int | None = None
        for pair in alignment.pairs:
            s = pair.secondary_line_index
            if s is not None and prev_s is not None and s < prev_s:
                order_errors += 1
            if s is not None:
                prev_s = s

    accuracy = correct_pairs / total_pairs if total_pairs else 0.0
    order_error_rate = order_errors / total_pairs if total_pairs else 0.0

    # Printed for CI log visibility; asserted as thresholds.
    print(
        f"#79 alignment groundtruth: "
        f"accuracy={accuracy:.2%} ({correct_pairs}/{total_pairs}), "
        f"order_error_rate={order_error_rate:.2%} ({order_errors}/{total_pairs})"
    )

    assert accuracy >= 0.95, f"alignment accuracy {accuracy:.2%} below 95%"
    assert order_error_rate == 0.0, f"order errors: {order_errors}"
