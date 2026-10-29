from __future__ import annotations

from scribeforge.domain.decisions import DecisionActor, adjudicate_pair
from scribeforge.domain.vlm import VLMReading


def _reading(text: str, *, uncertain: bool = False) -> VLMReading:
    return VLMReading(0, 0, "local-model", "1.0", text, uncertain)


def test_agreeing_engines_resolve_automatically() -> None:
    result = adjudicate_pair("鸦", "鸦", None)

    assert result.resolved
    assert result.actor is DecisionActor.AUTOMATIC
    assert result.selected_text == "鸦"
    assert result.rationale == "engines_agree"


def test_agreement_wins_over_an_uncertain_review() -> None:
    result = adjudicate_pair("鸦", "鸦", _reading("鸦", uncertain=True))

    assert result.actor is DecisionActor.AUTOMATIC
    assert result.selected_text == "鸦"


def test_confident_vlm_resolves_a_disagreement() -> None:
    result = adjudicate_pair("鸦", "雅", _reading("鸦"))

    assert result.actor is DecisionActor.VLM
    assert result.selected_text == "鸦"
    assert result.rationale == "vlm_confident"


def test_uncertain_vlm_stays_unresolved() -> None:
    result = adjudicate_pair("鸦", "雅", _reading("鸦", uncertain=True))

    assert not result.resolved
    assert result.selected_text is None
    assert result.actor is None
    assert result.rationale == "vlm_uncertain"


def test_blank_vlm_text_stays_unresolved() -> None:
    result = adjudicate_pair("鸦", "雅", _reading("   "))

    assert not result.resolved
    assert result.rationale == "vlm_blank"


def test_disagreement_without_review_stays_unresolved() -> None:
    result = adjudicate_pair("鸦", "雅", None)

    assert not result.resolved
    assert result.rationale == "engines_disagree"


def test_confident_vlm_can_resolve_a_single_engine_line() -> None:
    result = adjudicate_pair("鸦", None, _reading("鸦"))

    assert result.actor is DecisionActor.VLM
    assert result.selected_text == "鸦"


def test_single_engine_line_without_review_stays_unresolved() -> None:
    result = adjudicate_pair(None, "雅", None)

    assert not result.resolved
    assert result.rationale == "missing_engine_text"


def test_pair_without_any_engine_text_stays_unresolved() -> None:
    result = adjudicate_pair(None, None, None)

    assert not result.resolved
    assert result.rationale == "no_engine_text"


def test_a_confident_vlm_that_matches_no_engine_is_not_accepted() -> None:
    """Issue #76: a model's own confidence is not independent agreement."""

    result = adjudicate_pair("鸦", "雅", _reading("鸡"))

    assert not result.resolved
    assert result.actor is None
    assert result.rationale == "vlm_uncorroborated"


def test_a_vlm_reading_of_a_glyph_variant_still_corroborates() -> None:
    """Corroboration is measured on the folded form, as engine agreement is."""

    result = adjudicate_pair("为", "乌", _reading("爲"))

    assert result.actor is DecisionActor.VLM
    assert result.selected_text == "爲"
