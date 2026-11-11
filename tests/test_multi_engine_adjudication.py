"""Exhaustive tests for the N-engine adjudication rule (issue #90).

`adjudicate_readings` generalizes the two-engine `adjudicate_pair` to any
number of engines: independent agreement (exact or glyph-variant) is accepted
automatically, a strict majority cluster wins when engines split, and a
confident VLM reading corroborating *any* engine is accepted as the VLM actor.
Everything else — including three-way disagreement — stays queued for a
human. The rule is a pure function over engine texts plus optional VLM/risk.
"""

from __future__ import annotations

from scribeforge.domain.decisions import (
    DecisionActor,
    adjudicate_pair,
    adjudicate_readings,
)
from scribeforge.domain.vlm import VLMReading


def _reading(text: str, *, uncertain: bool = False) -> VLMReading:
    return VLMReading(0, 0, "local-model", "1.0", text, uncertain)


# --- all engines agree -------------------------------------------------------


def test_three_engines_identical_text_agree() -> None:
    result = adjudicate_readings(["鸦", "鸦", "鸦"], None)

    assert result.resolved
    assert result.actor is DecisionActor.AUTOMATIC
    assert result.selected_text == "鸦"
    assert result.rationale == "engines_agree"


def test_all_engines_in_one_variant_cluster_keep_first_literal() -> None:
    result = adjudicate_readings(["說", "说", "說"], None)

    assert result.resolved
    assert result.actor is DecisionActor.AUTOMATIC
    assert result.selected_text == "說"
    assert result.rationale == "variant_agreement"


# --- majority cluster wins ----------------------------------------------------


def test_two_of_three_identical_beats_the_odd_one() -> None:
    result = adjudicate_readings(["鸦", "雅", "鸦"], None)

    assert result.resolved
    assert result.actor is DecisionActor.AUTOMATIC
    assert result.selected_text == "鸦"
    assert result.rationale == "majority_agreement"


def test_variant_cluster_of_two_wins_and_keeps_first_literal() -> None:
    result = adjudicate_readings(["說", "雅", "说"], None)

    assert result.resolved
    assert result.selected_text == "說"
    assert result.rationale == "majority_agreement"


def test_majority_does_not_rewrite_the_majority_text() -> None:
    result = adjudicate_readings(["说", "說", "鸦"], None)

    assert result.selected_text == "说"
    assert result.rationale == "majority_agreement"


# --- no majority --------------------------------------------------------------


def test_three_way_disagreement_stays_queued_without_vlm() -> None:
    result = adjudicate_readings(["甲", "乙", "丙"], None)

    assert not result.resolved
    assert result.selected_text is None
    assert result.actor is None
    assert result.rationale == "engines_disagree"


def test_confident_vlm_corroborating_any_engine_resolves() -> None:
    result = adjudicate_readings(["甲", "乙", "丙"], _reading("乙"))

    assert result.resolved
    assert result.actor is DecisionActor.VLM
    assert result.selected_text == "乙"
    assert result.rationale == "vlm_confident"


def test_confident_vlm_matching_nothing_stays_queued() -> None:
    result = adjudicate_readings(["甲", "乙", "丙"], _reading("丁"))

    assert not result.resolved
    assert result.rationale == "vlm_uncorroborated"


def test_uncertain_vlm_never_resolves() -> None:
    result = adjudicate_readings(["甲", "乙", "丙"], _reading("甲", uncertain=True))

    assert not result.resolved
    assert result.rationale == "vlm_uncertain"


def test_blank_vlm_never_resolves() -> None:
    result = adjudicate_readings(["甲", "乙", "丙"], _reading("  "))

    assert not result.resolved
    assert result.rationale == "vlm_blank"


# --- missing engine outputs ---------------------------------------------------


def test_no_engine_text_at_all() -> None:
    result = adjudicate_readings([None, None, None], None)

    assert not result.resolved
    assert result.rationale == "no_engine_text"


def test_single_engine_text_needs_vlm_corroboration() -> None:
    unresolved = adjudicate_readings(["鸦", None, None], None)
    assert not unresolved.resolved
    assert unresolved.rationale == "missing_engine_text"

    resolved = adjudicate_readings(["鸦", None, None], _reading("鸦"))
    assert resolved.resolved
    assert resolved.actor is DecisionActor.VLM
    assert resolved.selected_text == "鸦"


def test_majority_survives_one_missing_engine() -> None:
    result = adjudicate_readings(["鸦", "鸦", None], None)

    assert result.resolved
    assert result.selected_text == "鸦"
    assert result.rationale == "engines_agree"


# --- acceptance gate still applies --------------------------------------------


def test_majority_text_is_still_gated() -> None:
    # A replacement character agreed on by a majority is still not text.
    result = adjudicate_readings(["\ufffd", "\ufffd", "雅"], None)

    assert not result.resolved
    assert result.blocked
    assert "unrepresentable_text" in result.blockers
    assert result.rationale == "majority_agreement"


# --- backward compatibility ----------------------------------------------------


def test_two_engine_wrapper_matches_the_general_rule() -> None:
    for primary, secondary, vlm in [
        ("鸦", "鸦", None),
        ("說", "说", None),
        ("鸦", "雅", None),
        ("鸦", "雅", _reading("鸦")),
        ("鸦", "雅", _reading("鳥")),
        ("鸦", None, None),
        (None, None, None),
    ]:
        legacy = adjudicate_pair(primary, secondary, vlm)
        general = adjudicate_readings([primary, secondary], vlm)
        assert legacy == general
