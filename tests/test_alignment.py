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


def test_alignment_rejects_different_pages() -> None:
    primary = _page("mineru", [("x", 0.1)])
    secondary = PageOCRResult(1, "paddleocr", "1", ())

    try:
        align_pages(primary, secondary)
    except ValueError as exc:
        assert "same page" in str(exc)
    else:
        raise AssertionError("expected page mismatch to fail")
