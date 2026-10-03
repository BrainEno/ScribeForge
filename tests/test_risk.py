from scribeforge.domain.alignment import align_pages
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import RiskReason, assess_page, review_candidates


def _line(text: str, y: float, confidence: float = 0.99) -> OCRLine:
    box = BoundingBox(0.1, y, 0.8, 0.05)
    return OCRLine(text, box, (OCRToken(text, confidence, box),))


def _page(engine: str, *lines: OCRLine) -> PageOCRResult:
    return PageOCRResult(0, engine, "1", tuple(lines))


def test_substitution_creates_material_disagreement_risk() -> None:
    primary = _page("mineru", _line("他望着那只乌鸦。", 0.1))
    secondary = _page("paddleocr", _line("他望着那只鸟鸦。", 0.1))
    alignment = align_pages(primary, secondary)

    risk = assess_page(alignment, primary, secondary)[0]

    assert RiskReason.TEXT_DISAGREEMENT in risk.reasons
    assert risk.score >= 0.4


def test_low_confidence_is_risky_even_when_engines_agree() -> None:
    primary = _page("mineru", _line("同一句。", 0.1, 0.55))
    secondary = _page("paddleocr", _line("同一句。", 0.1, 0.60))
    alignment = align_pages(primary, secondary)

    risk = assess_page(alignment, primary, secondary)[0]

    assert RiskReason.LOW_CONFIDENCE in risk.reasons
    assert risk.score > 0


def test_punctuation_disagreement_scores_below_text_substitution() -> None:
    punctuation_a = _page("mineru", _line("你好，世界。", 0.1))
    punctuation_b = _page("paddleocr", _line("你好,世界。", 0.1))
    text_a = _page("mineru", _line("乌鸦", 0.1))
    text_b = _page("paddleocr", _line("鸟鸦", 0.1))

    punctuation = assess_page(align_pages(punctuation_a, punctuation_b), punctuation_a, punctuation_b)[0]
    substitution = assess_page(align_pages(text_a, text_b), text_a, text_b)[0]

    assert punctuation.score < substitution.score


def test_unmatched_line_becomes_review_candidate_with_source_crop() -> None:
    primary = _page("mineru", _line("第一行", 0.1), _line("疑似漏行", 0.3))
    secondary = _page("paddleocr", _line("第一行", 0.1))
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)

    candidates = review_candidates(alignment, risks, primary, secondary, threshold=0.3)

    candidate = next(item for item in candidates if item.pair_index == 1)
    assert RiskReason.MISSING_OR_EXTRA_LINE in candidate.reasons
    assert candidate.crop == primary.lines[1].box


def test_unicode_replacement_character_is_flagged() -> None:
    primary = _page("mineru", _line("错�字", 0.1))
    secondary = _page("paddleocr", _line("错�字", 0.1))
    alignment = align_pages(primary, secondary)

    risk = assess_page(alignment, primary, secondary)[0]

    assert RiskReason.UNICODE_ANOMALY in risk.reasons


def test_clean_high_confidence_pair_does_not_enter_review_queue() -> None:
    primary = _page("mineru", _line("完全相同。", 0.1))
    secondary = _page("paddleocr", _line("完全相同。", 0.1))
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)

    assert risks[0].score == 0
    assert review_candidates(alignment, risks, primary, secondary, threshold=0.3) == ()
