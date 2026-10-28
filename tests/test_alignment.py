from scribeforge.domain.alignment import ConflictKind, align_pages
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult


def _page(engine: str, lines: list[tuple[str, float]]) -> PageOCRResult:
    return PageOCRResult(
        page_index=0,
        engine=engine,
        engine_version="1",
        lines=tuple(
            OCRLine(text, BoundingBox(0.1, y, 0.8, 0.05)) for text, y in lines
        ),
    )


def test_alignment_matches_nearby_lines_and_classifies_substitution() -> None:
    primary = _page("mineru", [("他望着那只乌鸦。", 0.10)])
    secondary = _page("paddleocr", [("他望着那只鸟鸦。", 0.11)])

    result = align_pages(primary, secondary)

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.primary_line_index == 0
    assert pair.secondary_line_index == 0
    assert pair.conflicts[0].kind is ConflictKind.SUBSTITUTION
    assert pair.conflicts[0].primary_text == "乌"
    assert pair.conflicts[0].secondary_text == "鸟"


def test_alignment_classifies_punctuation_change_separately() -> None:
    primary = _page("mineru", [("你好，世界。", 0.20)])
    secondary = _page("paddleocr", [("你好,世界。", 0.20)])

    pair = align_pages(primary, secondary).pairs[0]

    assert pair.conflicts[0].kind is ConflictKind.PUNCTUATION
    assert pair.conflicts[0].primary_text == "，"
    assert pair.conflicts[0].secondary_text == ","


def test_alignment_preserves_unmatched_line_as_deletion() -> None:
    primary = _page("mineru", [("第一行", 0.10), ("第二行", 0.20)])
    secondary = _page("paddleocr", [("第一行", 0.10)])

    result = align_pages(primary, secondary)

    assert len(result.pairs) == 2
    assert result.pairs[1].secondary_line_index is None
    assert result.pairs[1].conflicts[0].kind is ConflictKind.DELETION
    assert result.pairs[1].conflicts[0].primary_text == "第二行"


def test_alignment_preserves_secondary_only_line_as_insertion() -> None:
    primary = _page("mineru", [("第一行", 0.10)])
    secondary = _page("paddleocr", [("第一行", 0.10), ("新增行", 0.30)])

    result = align_pages(primary, secondary)

    assert result.pairs[1].primary_line_index is None
    assert result.pairs[1].conflicts[0].kind is ConflictKind.INSERTION
    assert result.pairs[1].conflicts[0].secondary_text == "新增行"


def test_exact_match_has_no_conflicts() -> None:
    primary = _page("mineru", [("完全相同。", 0.10)])
    secondary = _page("paddleocr", [("完全相同。", 0.10)])

    pair = align_pages(primary, secondary).pairs[0]

    assert pair.text_similarity == 1.0
    assert pair.vertical_distance == 0.0
    assert pair.conflicts == ()


def test_alignment_prefers_globally_optimal_assignment_over_greedy_order() -> None:
    # "number" is a slightly-better-than-coincidence match for "lumber" (0.83),
    # but "lumber" is actually "plumber"'s true counterpart (0.92). Processing
    # primary lines strictly in order must not let an earlier line claim a
    # candidate that is a stronger match for a later line, leaving the later
    # line stranded with a poor match ("rubber" vs "plumber" at 0.62) when the
    # globally better pairing ("number"-"rubber" at 0.67, "plumber"-"lumber"
    # at 0.92) is available.
    primary = _page("mineru", [("number", 0.10), ("plumber", 0.14)])
    secondary = _page("paddleocr", [("lumber", 0.11), ("rubber", 0.13)])

    result = align_pages(primary, secondary)

    assert len(result.pairs) == 2
    by_primary = {pair.primary_line_index: pair for pair in result.pairs}
    assert by_primary[0].secondary_line_index == 1
    assert by_primary[1].secondary_line_index == 0


def test_alignment_rejects_different_pages() -> None:
    primary = _page("mineru", [("x", 0.1)])
    secondary = PageOCRResult(1, "paddleocr", "1", ())

    try:
        align_pages(primary, secondary)
    except ValueError as exc:
        assert "same page" in str(exc)
    else:
        raise AssertionError("expected page mismatch to fail")


def test_alignment_skips_similarity_for_vertically_distant_lines(monkeypatch) -> None:
    """Lines further apart than the vertical threshold can never pair.

    Computing a full text similarity for those candidates is wasted work that
    grows quadratically with page density: a 40-line page evaluates 1,600
    SequenceMatcher calls even though only a few dozen are plausible. The
    distance filter must therefore run before similarity is computed, and the
    result must stay identical.
    """

    from scribeforge.domain import alignment as alignment_module

    calls = {"count": 0}
    real_matcher = alignment_module.SequenceMatcher

    class CountingMatcher(real_matcher):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            calls["count"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(alignment_module, "SequenceMatcher", CountingMatcher)

    lines = [(f"line-{index}", 0.05 + index * 0.02) for index in range(20)]
    primary = _page("mineru", lines)
    secondary = _page("paddleocr", lines)

    result = align_pages(primary, secondary)

    assert len(result.pairs) == 20
    assert all(pair.secondary_line_index is not None for pair in result.pairs)
    # Only vertically plausible neighbours are compared (plus one diff per
    # matched pair), never the full 400-pair grid.
    assert calls["count"] <= 200, f"computed {calls['count']} similarities for 20 lines"
