"""Paragraph reconstruction: the rules, and how well they do on real text."""

from __future__ import annotations

from pathlib import Path

import pytest

from scribeforge.domain.content import VerifiedPage
from scribeforge.domain.ocr import BoundingBox, PageDirection
from scribeforge.domain.paragraphs import (
    LineRole,
    TextLine,
    assemble_paragraphs,
)

CORPUS = Path(__file__).resolve().parent.parent / "benchmark" / "corpus" / "v"

#: Left margin and measure of a typical 32-open page, in normalized units.
LEFT = 0.06
RIGHT = 0.88
#: Line pitch, in normalized units; a full page is thirty lines and still fits.
PITCH = 0.028
#: Characters on a full line in this book.
MEASURE = 28


def _line(
    text: str, index: int, *, indent: int = 0, role: LineRole = LineRole.BODY
) -> TextLine:
    """One visual line at the given row; ``indent`` shifts it in characters."""

    char_width = (RIGHT - LEFT) / MEASURE
    x = LEFT + indent * char_width
    width = char_width * len(text)
    y = 0.06 + index * PITCH
    return TextLine(text, BoundingBox(x, y, width, PITCH * 0.7), role)


def _page(*lines: TextLine) -> tuple[str, ...]:
    return tuple(
        paragraph.text
        for paragraph in assemble_paragraphs(lines, PageDirection.HORIZONTAL_LR)
    )


def test_an_indented_short_ended_paragraph_starts_a_new_one() -> None:
    """The common case: paragraph ended before the measure, next line is indented."""

    lines = (
        _line("甲乙丙丁戊己庚辛", 0, indent=2),
        _line("壬癸子丑寅卯。", 1, indent=0),
        _line("辰巳午未申酉", 2, indent=2),
        _line("戌亥。", 3),
    )

    assert _page(*lines) == ("甲乙丙丁戊己庚辛壬癸子丑寅卯。", "辰巳午未申酉戌亥。")


def test_a_full_measure_line_ending_in_a_full_stop_continues() -> None:
    """Chinese typesetters continue on the next line when a stop lands at the margin.

    Breaking here is the mistake a punctuation-only rule makes: it turns one
    paragraph into two, and the second one starts mid-sentence.
    """

    full = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"  # MEASURE characters
    lines = (
        _line(full + "。", 0, indent=2),
        _line("辰巳午未申酉戌亥。", 1),
    )

    assert _page(*lines) == (full + "。辰巳午未申酉戌亥。",)


def test_an_unfinished_line_joins_the_next_one() -> None:
    lines = (
        _line("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午", 0, indent=2),
        _line("未申酉戌亥。", 1),
    )

    assert _page(*lines) == ("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥。",)


def test_a_larger_gap_starts_a_new_paragraph() -> None:
    """A deliberately widened gap is a separation even without an indent."""

    first = _line("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥", 0, indent=2)
    second = TextLine(
        "辰巳午未。", BoundingBox(LEFT, 0.1 + PITCH * 0.7 + PITCH * 2.5, 0.2, PITCH * 0.7)
    )
    third = TextLine(
        "申酉戌亥。", BoundingBox(LEFT, 0.1 + PITCH * 3.2 + PITCH * 2.5, 0.2, PITCH * 0.7)
    )

    paragraphs = _page(first, second, third)

    assert paragraphs == ("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥辰巳午未。", "申酉戌亥。")


def test_latin_broken_across_lines_keeps_its_space() -> None:
    """A break inside a Latin phrase must not weld two words together."""

    lines = (
        _line("see the Inter", 0, indent=2),
        _line("national style", 1),
    )

    assert _page(*lines) == ("see the Inter national style",)


def test_heading_and_footnote_are_their_own_units() -> None:
    lines = (
        _line("第 一 章", 0, role=LineRole.HEADING),
        _line("本尼·普鲁费恩，", 1, indent=2),
        _line("一个大傻瓜蛋，", 2),
        _line("① 远手点：指溜溜球离开手最远的那一点。", 3, role=LineRole.FOOTNOTE),
    )

    paragraphs = assemble_paragraphs(list(lines), PageDirection.HORIZONTAL_LR)

    assert [(p.role, p.text) for p in paragraphs] == [
        (LineRole.HEADING, "第 一 章"),
        (LineRole.BODY, "本尼·普鲁费恩，一个大傻瓜蛋，"),
        (LineRole.FOOTNOTE, "① 远手点：指溜溜球离开手最远的那一点。"),
    ]


def test_two_consecutive_headings_stay_separate() -> None:
    lines = (
        _line("第一章", 0, role=LineRole.HEADING),
        _line("第一节", 1, role=LineRole.HEADING),
    )

    paragraphs = assemble_paragraphs(list(lines), PageDirection.HORIZONTAL_LR)

    assert [p.text for p in paragraphs] == ["第一章", "第一节"]


def test_a_blank_line_separates_without_becoming_a_paragraph() -> None:
    lines = (
        _line("甲乙", 0, indent=2),
        TextLine("", BoundingBox(LEFT, 0.1 + PITCH, 0.0, PITCH)),
        _line("丙丁", 2, indent=2),
    )

    assert _page(*lines) == ("甲乙", "丙丁")


def test_text_only_input_still_groups_lines() -> None:
    """Without geometry the punctuation rule is all that is left, and it is used."""

    lines = [TextLine("甲乙丙丁，"), TextLine("戊己。"), TextLine("庚辛")]

    assert _page(*lines) == ("甲乙丙丁，戊己。", "庚辛")


def test_vertical_columns_join_until_a_sentence_ends() -> None:
    lines = (
        TextLine("甲乙丙", BoundingBox(0.7, 0.1, 0.05, 0.8)),
        TextLine("丁戊己。", BoundingBox(0.6, 0.1, 0.05, 0.8)),
        TextLine("庚辛。", BoundingBox(0.5, 0.1, 0.05, 0.8)),
    )

    paragraphs = assemble_paragraphs(list(lines), PageDirection.VERTICAL_RL)

    assert [p.text for p in paragraphs] == ["甲乙丙丁戊己。", "庚辛。"]


def test_a_verified_page_rebuilds_paragraphs_from_its_layout_evidence() -> None:
    """The page carries the evidence; the export must not re-guess it."""

    lines = (
        "甲乙丙丁戊己庚辛",
        "壬癸子丑寅卯。",
        "第 一 章",
    )
    page = VerifiedPage(
        page_index=0,
        lines=lines,
        line_boxes=(
            BoundingBox(LEFT + 0.02, 0.1, 0.7, 0.02),
            BoundingBox(LEFT, 0.14, 0.3, 0.02),
            BoundingBox(LEFT, 0.30, 0.2, 0.04),
        ),
        line_roles=(LineRole.BODY, LineRole.BODY, LineRole.HEADING),
    )

    assert [(block.role, block.text) for block in page.blocks()] == [
        (LineRole.BODY, "甲乙丙丁戊己庚辛壬癸子丑寅卯。"),
        (LineRole.HEADING, "第 一 章"),
    ]
    # ``lines`` stays the accepted evidence, one entry per OCR line, never rewritten.
    assert page.lines == lines


def test_layout_evidence_must_match_the_accepted_lines() -> None:
    with pytest.raises(ValueError, match="one to one"):
        VerifiedPage(
            page_index=0,
            lines=("甲乙。", "丙丁。"),
            line_roles=(LineRole.BODY,),
        )


def test_empty_page_has_no_paragraphs() -> None:
    assert assemble_paragraphs([], PageDirection.HORIZONTAL_LR) == ()


def _rebreak(paragraph: str, measure: int) -> list[str]:
    """Break a paragraph into visual lines the way a typesetter would.

    The first line is two characters shorter because it is indented, which is the
    whole point: an indented first line ends *at* the measure, not past it.
    """

    pieces: list[str] = []
    cursor = 0
    length = measure - 2
    while cursor < len(paragraph):
        end = min(cursor + length, len(paragraph))
        # A Latin word is not broken without a hyphen: the word moves down whole.
        # Splitting one here would prove nothing about the joiner and would push a
        # space into the text, since the joiner spaces Latin-Latin joins.
        while (
            end < len(paragraph)
            and end > cursor + 1
            and paragraph[end - 1].isascii()
            and paragraph[end].isascii()
            and paragraph[end - 1].isalnum()
            and paragraph[end].isalnum()
        ):
            end -= 1
        pieces.append(paragraph[cursor:end])
        cursor = end
        length = measure
    return pieces


def _rebuild_page(paragraphs: list[str], measure: int) -> list[TextLine]:
    lines: list[TextLine] = []
    row = 0
    for paragraph in paragraphs:
        for piece_index, piece in enumerate(_rebreak(paragraph, measure)):
            lines.append(_line(piece, row, indent=2 if piece_index == 0 else 0))
            row += 1
    return lines


@pytest.mark.benchmark
def test_paragraph_recovery_on_the_real_corpus_text() -> None:
    """Measure the joiner on the real transcriptions, not on invented prose.

    There is no OCR in this environment, so the visual lines are produced by
    re-breaking the hand transcription at the book's measure: the *text* is real
    and the paragraph boundaries are real, only the line breaks are synthetic.
    That is exactly the input the joiner exists to undo.
    """

    manifest = CORPUS / "manifest.json"
    if not manifest.is_file():
        pytest.skip("benchmark corpus is not available")

    import json

    data = json.loads(manifest.read_text(encoding="utf-8"))
    recovered_total = 0
    expected_total = 0
    lost_characters = 0
    for case in data["cases"]:
        truth = (CORPUS / case["ground_truth"]).read_text(encoding="utf-8").splitlines()
        truth = [line for line in truth if line.strip()]
        lines = _rebuild_page(truth, MEASURE)
        got = [p.text for p in assemble_paragraphs(lines, PageDirection.HORIZONTAL_LR)]

        # A page whose paragraph count is wrong is wrong even if its first few
        # paragraphs happen to match, and a ratio alone would hide that.
        assert len(got) == len(truth), (
            f"page {case['id']}: {len(got)} paragraphs, expected {len(truth)}"
        )
        expected_total += len(truth)
        recovered_total += sum(1 for a, b in zip(truth, got, strict=False) if a == b)
        # A join across a Latin word break legitimately adds a space, so compare
        # without whitespace: the question is whether a character was lost.
        lost_characters += sum(len("".join(text.split())) for text in truth) - sum(
            len("".join(text.split())) for text in got
        )

    # A page whose paragraph count came out wrong shifts every later comparison,
    # so exact per-paragraph equality is a strict measure. It is the right one:
    # a wrongly split paragraph is a visible defect in the reading experience.
    assert recovered_total / expected_total >= 0.95
    assert lost_characters == 0
