"""Tests for glyph-variant folding (issue #88).

The central guarantee under test is that folding changes *comparison*, never
text that a reader or a decision sees.
"""

from __future__ import annotations

import pytest

from scribeforge.domain.alignment import align_pages
from scribeforge.domain.decisions import DecisionActor, adjudicate_pair
from scribeforge.domain.normalize import (
    VARIANT_GROUPS,
    fold_chars,
    fold_for_comparison,
    is_variant_only_difference,
    similarity_for_comparison,
    variant_groups_in,
)
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.risk import RiskReason, assess_page

# ------------------------------------------------------------------ folding


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("爲什麼", "为什么"),
        ("說", "说"),
        ("這個", "这个"),
        ("鴉片", "鸦片"),
        ("裏面", "里面"),
        ("書", "书"),
        ("羣", "群"),
        ("峯", "峰"),
    ],
)
def test_traditional_and_variant_forms_fold_together(left: str, right: str) -> None:
    assert fold_for_comparison(left) == fold_for_comparison(right)


def test_compatibility_forms_fold_via_nfkc() -> None:
    """Full-width latin and CJK compatibility ideographs come out of scans freely."""

    assert fold_for_comparison("ＡＢＣ１２３") == fold_for_comparison("abc123")


def test_fold_for_comparison_removes_whitespace_and_folds_punctuation() -> None:
    left = "他說：「好」。"
    right = '他说:"好".'

    assert fold_for_comparison(left) == fold_for_comparison(right)


def test_fold_chars_keeps_the_text_readable() -> None:
    """``fold_chars`` is a light fold: it does not strip spacing or punctuation."""

    assert fold_chars("這是 一本書") == "这是 一本书"


def test_variant_table_has_no_contradictory_entries() -> None:
    """Every character must fold to exactly one canonical form."""

    assert len(VARIANT_GROUPS) > 300
    assert VARIANT_GROUPS["說"] == "说"
    assert VARIANT_GROUPS["羣"] == "群"


def test_is_variant_only_difference_distinguishes_agreement_from_variation() -> None:
    assert is_variant_only_difference("說", "说")
    assert not is_variant_only_difference("說", "說")
    assert not is_variant_only_difference("說", "讀")


def test_is_variant_only_difference_rejects_real_textual_difference() -> None:
    """Folding must never merge genuinely different words."""

    assert not is_variant_only_difference("他們走了", "他們跑了")


# ------------------------------------------------------------------ similarity


def test_similarity_ignores_variant_choice() -> None:
    assert similarity_for_comparison("鳥鴉飛過天空", "鸟鸦飞过天空") == 1.0


def test_similarity_falls_back_sanely_on_empty_input() -> None:
    assert similarity_for_comparison("", "") == 1.0
    assert similarity_for_comparison("字", "") == 0.0


def test_variant_groups_in_reports_observed_groups() -> None:
    groups = variant_groups_in(["說", "説", "書"])

    assert set(groups) == {"说", "书"}
    assert set(groups["说"]) == {"说", "說", "説"}


# ------------------------------------------------------------ alignment wiring


def _line(text: str, y: float) -> OCRLine:
    return OCRLine(text, BoundingBox(0.1, y, 0.8, 0.03), ())


def _page(lines: tuple[OCRLine, ...], engine: str) -> PageOCRResult:
    return PageOCRResult(0, engine, "1.0", lines)


def test_alignment_pairs_variant_only_lines_by_text_not_distance() -> None:
    """Two engines reading the same line in different scripts must still pair."""

    primary = _page((_line("這是第一行", 0.10), _line("這是第二行", 0.14)), "primary")
    secondary = _page((_line("这是第一行", 0.105), _line("这是第二行", 0.145)), "secondary")

    alignment = align_pages(primary, secondary)

    assert len(alignment.pairs) == 2
    assert all(pair.text_similarity >= 0.99 for pair in alignment.pairs)


# --------------------------------------------------------------------- risk


def test_variant_only_disagreement_scores_below_the_review_threshold() -> None:
    """A 繁/简 difference is a finding, not an incident."""

    primary = _page((_line("這是一行正文", 0.2),), "primary")
    secondary = _page((_line("这是一行正文", 0.2),), "secondary")

    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)

    assert risks[0].score < 0.35
    assert RiskReason.VARIANT_DISAGREEMENT in risks[0].reasons
    assert RiskReason.TEXT_DISAGREEMENT not in risks[0].reasons


def test_a_real_substitution_still_scores_as_disagreement() -> None:
    primary = _page((_line("這是第一行", 0.2),), "primary")
    secondary = _page((_line("這是第二行", 0.2),), "secondary")

    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)

    assert RiskReason.TEXT_DISAGREEMENT in risks[0].reasons
    assert risks[0].score >= 0.45


# -------------------------------------------------------------- adjudication


def test_variant_agreement_is_accepted_and_keeps_the_primary_text() -> None:
    """The accepted text is what an engine actually produced, never a fold."""

    decision = adjudicate_pair("這是正文", "这是正文", None)

    assert decision.selected_text == "這是正文"
    assert decision.actor is DecisionActor.AUTOMATIC
    assert decision.rationale == "variant_agreement"


def test_variant_agreement_does_not_hide_a_missing_engine_reading() -> None:
    decision = adjudicate_pair("這是正文", None, None)

    assert decision.selected_text is None
    assert decision.rationale == "missing_engine_text"


def test_identical_text_keeps_the_original_reason() -> None:
    decision = adjudicate_pair("正文", "正文", None)

    assert decision.rationale == "engines_agree"


def test_real_disagreement_still_requires_review() -> None:
    decision = adjudicate_pair("他走了", "他跑了", None)

    assert decision.selected_text is None
    assert decision.rationale == "engines_disagree"
