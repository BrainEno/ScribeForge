"""Source-derived group text assembly and whitespace-integrity regressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.alignment import AlignmentStatus, ConflictKind, align_pages
from scribeforge.domain.decisions import DecisionActor, adjudicate_pair
from scribeforge.domain.normalize import fold_for_comparison, is_variant_only_difference
from scribeforge.domain.ocr import BlockType, BoundingBox, OCRBlock, OCRLine, PageOCRResult
from scribeforge.domain.risk import RiskReason, assess_page, review_candidates
from scribeforge.domain.text_assembly import assemble_group_text
from scribeforge.pipeline.adjudication import adjudicate_verification, review_detail
from scribeforge.pipeline.assembly import assemble_verified_content
from scribeforge.storage.sqlite import SQLiteStore


def _page(engine: str, texts: list[str]) -> PageOCRResult:
    lines: list[OCRLine] = []
    blocks: list[OCRBlock] = []
    for index, text in enumerate(texts):
        box = BoundingBox(0.10, 0.20 + index * 0.04, 0.80, 0.04)
        lines.append(OCRLine(text, box))
        blocks.append(OCRBlock(BlockType.TEXT, box, index, index + 1))
    return PageOCRResult(0, engine, "fixture-1", tuple(lines), blocks=tuple(blocks))


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (["The quick", "brown fox"], "The quick brown fox"),
        (["The quick ", "brown fox"], "The quick brown fox"),
        (["The quick", " brown fox"], "The quick brown fox"),
        (["well-", "known"], "well-known"),
        (["prefix", "(suffix)"], "prefix(suffix)"),
        (["prefix", ", suffix"], "prefix, suffix"),
        (["春眠", "不覺曉"], "春眠不覺曉"),
        (["これは", "日本語です"], "これは日本語です"),
        (["混合 English", "text 完成"], "混合 English text 完成"),
        (["Figure", "2"], "Figure 2"),
        (["202", "4"], "2024"),
    ],
)
def test_group_text_assembly_preserves_source_and_script_boundaries(
    texts: list[str], expected: str
) -> None:
    source = tuple(texts)
    assert assemble_group_text(source) == expected
    assert source == tuple(texts)


@pytest.mark.parametrize(
    ("primary_texts", "secondary_texts", "expected", "expected_group"),
    [
        (
            ["The quick brown fox"],
            ["The quick", "brown fox"],
            "The quick brown fox",
            ((0,), (0, 1)),
        ),
        (
            ["The quick", "brown fox"],
            ["The quick brown fox"],
            "The quick brown fox",
            ((0, 1), (0,)),
        ),
        (
            ["The quick brown fox"],
            ["The", "quick brown fox"],
            "The quick brown fox",
            ((0,), (0, 1)),
        ),
        (
            ["混合 English text 完成"],
            ["混合 English", "text 完成"],
            "混合 English text 完成",
            ((0,), (0, 1)),
        ),
        (
            ["春眠不覺曉"],
            ["春眠", "不覺曉"],
            "春眠不覺曉",
            ((0,), (0, 1)),
        ),
        (
            ["これは日本語です"],
            ["これは", "日本語です"],
            "これは日本語です",
            ((0,), (0, 1)),
        ),
    ],
    ids=["english-1to2", "english-2to1", "different-break", "mixed", "traditional", "japanese"],
)
def test_multiline_text_semantics_match_without_losing_required_spaces(
    primary_texts: list[str],
    secondary_texts: list[str],
    expected: str,
    expected_group: tuple[tuple[int, ...], tuple[int, ...]],
) -> None:
    primary = _page("mineru", primary_texts)
    secondary = _page("paddleocr", secondary_texts)

    result = align_pages(primary, secondary)

    assert len(result.pairs) == 1
    pair = result.pairs[0]
    assert pair.status is AlignmentStatus.MATCHED
    assert (pair.primary_line_indexes, pair.secondary_line_indexes) == expected_group
    assert pair.conflicts == ()
    assert assemble_group_text(line.text for line in primary.lines) == expected
    assert assemble_group_text(line.text for line in secondary.lines) == expected


def test_uncertain_latin_word_internal_break_is_not_silently_accepted() -> None:
    primary = _page("mineru", ["international"])
    secondary = _page("paddleocr", ["inter", "national"])

    pair = align_pages(primary, secondary).pairs[0]

    assert pair.status is AlignmentStatus.MATCHED
    assert pair.conflicts
    assert any(conflict.kind is ConflictKind.INSERTION for conflict in pair.conflicts)
    decision = adjudicate_pair("international", "inter national", None)
    assert decision.selected_text is None


def test_whitespace_only_difference_is_not_a_glyph_variant_or_auto_accepted() -> None:
    assert fold_for_comparison("New York") != fold_for_comparison("NewYork")
    assert fold_for_comparison("New York") != fold_for_comparison("New  York")
    assert fold_for_comparison("New York") != fold_for_comparison("New\u3000York")
    assert fold_for_comparison("New York") != fold_for_comparison("New\u00a0York")
    assert not is_variant_only_difference("New York", "NewYork")
    assert not is_variant_only_difference("New York", "New\u3000York")
    assert not is_variant_only_difference("第 一 章", "第一章")
    assert not is_variant_only_difference("第 一 章", "第\u3000一\u3000章")

    for left, right in (
        ("New York", "NewYork"),
        ("New York", "New  York"),
        ("New York", "New\u3000York"),
        ("New York", "New\u00a0York"),
    ):
        decision = adjudicate_pair(left, right, None)
        assert decision.selected_text is None
        assert decision.rationale == "engines_disagree"


def test_traditional_variant_comparison_behavior_is_preserved() -> None:
    decision = adjudicate_pair("這是正文", "这是正文", None)
    assert decision.selected_text == "這是正文"
    assert decision.actor is DecisionActor.AUTOMATIC
    assert decision.rationale == "variant_agreement"


def test_space_difference_has_real_review_risk() -> None:
    primary = _page("mineru", ["New York"])
    secondary = _page("paddleocr", ["NewYork"])

    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)

    assert RiskReason.MISSING_OR_EXTRA_LINE in risks[0].reasons
    assert RiskReason.VARIANT_DISAGREEMENT not in risks[0].reasons


def test_machine_acceptance_and_export_keep_spaces_for_split_primary_lines(
    tmp_path: Path,
) -> None:
    primary = _page("mineru", ["The quick", "brown fox"])
    secondary = _page("paddleocr", ["The quick brown fox"])
    original_primary = tuple(primary.lines)
    original_secondary = tuple(secondary.lines)
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "page.png", 1000, 1000)
    primary_id = store.record_ocr(project_id, primary)
    secondary_id = store.record_ocr(project_id, secondary)
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary)
    verification_id = store.record_verification(
        primary_id, secondary_id, alignment, risks, candidates
    )

    outcome = adjudicate_verification(store, project_id, verification_id)
    decision = store.latest_decision(project_id, 0, 0)
    detail = review_detail(store, project_id, 0, 0)
    book = assemble_verified_content(store, project_id)

    assert outcome.recorded == 1
    assert decision is not None
    assert decision.actor == "automatic"
    assert decision.selected_text == "The quick brown fox"
    assert detail.primary_text == detail.secondary_text == "The quick brown fox"
    assert book.pages[0].lines == ("The quick brown fox",)
    assert store.load_ocr_run(primary_id).lines == original_primary
    assert store.load_ocr_run(secondary_id).lines == original_secondary
