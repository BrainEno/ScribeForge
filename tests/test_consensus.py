"""The agreement rule that decides whether a re-read may be accepted."""

from __future__ import annotations

import pytest

from scribeforge.domain.consensus import Reading, consensus


def test_two_readers_agreeing_is_enough() -> None:
    verdict = consensus([Reading("a", "鸦"), Reading("b", "鸦")])

    assert verdict.settled
    assert verdict.text == "鸦"
    assert verdict.supporters == ("a", "b")
    assert verdict.rationale == "unanimous"


def test_one_reader_is_never_a_consensus() -> None:
    """Issue #76: self-reported confidence alone must not accept a text."""

    verdict = consensus([Reading("vlm", "鸦")])

    assert not verdict.settled
    assert verdict.text is None
    assert verdict.rationale == "single_reader"


def test_a_majority_beats_a_lone_dissenter() -> None:
    verdict = consensus([Reading("a", "鸦"), Reading("b", "鸦"), Reading("c", "雅")])

    assert verdict.settled
    assert verdict.text == "鸦"
    assert verdict.supporters == ("a", "b")
    assert verdict.rationale == "majority"


def test_a_tie_between_two_supported_groups_stays_open() -> None:
    """Adding a reader must reduce ambiguity, never manufacture a winner."""

    verdict = consensus(
        [
            Reading("a", "鸦"),
            Reading("b", "鸦"),
            Reading("c", "雅"),
            Reading("d", "雅"),
        ]
    )

    assert not verdict.settled
    assert verdict.rationale == "tie"


def test_glyph_variant_readings_count_as_agreement_but_keep_literal_text() -> None:
    verdict = consensus(
        [Reading("a", "为"), Reading("b", "爲")], prefer=["a", "b"]
    )

    assert verdict.settled
    # Agreement is measured on the folded form; the accepted characters are one
    # reader's literal characters, never a normalized rewrite.
    assert verdict.text == "为"


def test_uncertain_and_blank_readings_never_vote() -> None:
    """An unsure reading cannot be the second vote that settles a pair."""

    verdict = consensus(
        [
            Reading("a", "鸦"),
            Reading("b", "鸦", uncertain=True),
            Reading("c", "  "),
            Reading("d", None),
        ]
    )

    assert not verdict.settled
    assert verdict.rationale == "single_reader"
    assert verdict.considered == ("a",)

    settled = consensus(
        [
            Reading("a", "鸦"),
            Reading("b", "鸦", uncertain=True),
            Reading("e", "鸦"),
        ]
    )

    assert settled.settled
    assert settled.supporters == ("a", "e")


def test_no_usable_reading_reports_why() -> None:
    verdict = consensus([Reading("a", None), Reading("b", "")])

    assert not verdict.settled
    assert verdict.rationale == "no_reading"
    assert verdict.considered == ()


def test_prefer_order_picks_the_spelling_when_variants_agree() -> None:
    verdict = consensus(
        [Reading("late", "爲"), Reading("early", "为")], prefer=["early", "late"]
    )

    assert verdict.text == "为"


def test_an_exact_duplicate_outranks_the_preference_order() -> None:
    verdict = consensus(
        [Reading("a", "为"), Reading("b", "爲"), Reading("c", "爲")],
        prefer=["a", "b", "c"],
    )

    assert verdict.text == "爲"


def test_a_reading_must_name_its_reader() -> None:
    with pytest.raises(ValueError, match="reader"):
        Reading("  ", "鸦")
