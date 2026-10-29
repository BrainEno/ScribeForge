"""The acceptance gate: may a machine certify this text without a person?

These tests pin the boundary the pipeline must never cross on its own. They are
deliberately small and boring, because the failure they guard against is quiet: an
exported book that looks finished and contains text no reader can trust (#75).
"""

from __future__ import annotations

import pytest

from scribeforge.domain.acceptance import (
    DEFAULT_POLICY,
    AcceptancePolicy,
    AcceptanceSummary,
    SuspectAcceptance,
    describe_blockers,
    has_unrepresentable_characters,
    machine_blockers,
    unrepresentable_characters,
)
from scribeforge.domain.risk import PairRisk, RiskReason


def _risk(
    score: float = 0.0,
    reasons: tuple[RiskReason, ...] = (),
    *,
    min_confidence: float | None = 0.9,
    content_score: float | None = None,
) -> PairRisk:
    return PairRisk(
        pair_index=0,
        score=score,
        reasons=reasons,
        min_confidence=min_confidence,
        content_score=score if content_score is None else content_score,
    )


def test_plain_agreed_text_is_accepted() -> None:
    assert machine_blockers(text="子曰學而時習之", risk=_risk()) == ()


def test_text_without_any_risk_row_is_still_judged_on_its_text() -> None:
    assert machine_blockers(text="\ufffd", risk=None) == (
        "unrepresentable_text",
    )
    assert machine_blockers(text="子曰", risk=None) == ()


def test_replacement_character_blocks_acceptance() -> None:
    """U+FFFD is an engine saying 'I could not read this glyph', not text."""

    assert has_unrepresentable_characters("子曰\ufffd")
    assert unrepresentable_characters("子曰\ufffd也") == ("U+FFFD",)
    assert machine_blockers(text="子曰\ufffd", risk=_risk()) == (
        "unrepresentable_text",
    )


@pytest.mark.parametrize("control", ["\x00", "\x0b", "\x1b", "\x7f"])
def test_control_characters_block_acceptance(control: str) -> None:
    assert machine_blockers(text=f"子曰{control}", risk=_risk()) == (
        "unrepresentable_text",
    )
    assert unrepresentable_characters(f"a{control}") == (f"U+{ord(control):04X}",)


def test_export_legal_whitespace_is_not_a_defect() -> None:
    assert not has_unrepresentable_characters("子\t曰\n學\r而時習之")
    assert machine_blockers(text="子\t曰", risk=_risk()) == ()


def test_blank_text_blocks_acceptance() -> None:
    assert machine_blockers(text="   ", risk=_risk()) == ("blank_text",)
    assert machine_blockers(text="", risk=None) == ("blank_text",)


def test_high_content_risk_blocks_acceptance() -> None:
    risk = _risk(
        0.6, (RiskReason.LOW_CONFIDENCE, RiskReason.UNICODE_ANOMALY), min_confidence=0.1
    )
    assert machine_blockers(text="子曰", risk=risk) == (
        "unicode_anomaly",
        "high_content_risk",
        "low_confidence",
    )


def test_structural_disagreement_alone_does_not_block() -> None:
    """A VLM-resolved disagreement is a settled question, not a risk to export.

    The gate reads the *content* score: if it read the total score, every pair a
    vision model settled would be blocked and the feature would be dead on arrival.
    """

    risk = _risk(
        score=0.45,
        reasons=(RiskReason.TEXT_DISAGREEMENT,),
        min_confidence=0.9,
        content_score=0.0,
    )
    assert machine_blockers(text="子曰", risk=risk) == ()


def test_blocking_reasons_block_whatever_the_score() -> None:
    """A spatial mismatch means the two boxes may not even be the same line."""

    risk = _risk(
        score=0.15,
        reasons=(RiskReason.SPATIAL_MISMATCH,),
        min_confidence=0.95,
        content_score=0.15,
    )
    assert machine_blockers(text="子曰", risk=risk) == ("spatial_mismatch",)


def test_low_confidence_blocks_acceptance() -> None:
    """Two engines guessing the same way is not corroboration."""

    risk = _risk(0.0, (), min_confidence=0.1, content_score=0.0)
    assert machine_blockers(text="鸦", risk=_risk(min_confidence=0.1)) == (
        "low_confidence",
    )
    assert risk.min_confidence == 0.1


def test_confidence_at_the_floor_is_enough() -> None:
    policy = DEFAULT_POLICY
    assert machine_blockers(
        text="子曰", risk=_risk(min_confidence=policy.min_confidence)
    ) == ()


def test_unknown_confidence_does_not_block_on_confidence() -> None:
    """An adapter that reports no confidence is not the same as a low one."""

    assert machine_blockers(
        text="子曰", risk=_risk(0.0, (), min_confidence=None)
    ) == ()


def test_reasons_are_reported_in_a_stable_order() -> None:
    risk = _risk(
        0.6, (RiskReason.SPATIAL_MISMATCH, RiskReason.UNICODE_ANOMALY), min_confidence=0.1
    )
    assert machine_blockers(text="\ufffd", risk=risk) == (
        "unrepresentable_text",
        "unicode_anomaly",
        "spatial_mismatch",
        "high_content_risk",
        "low_confidence",
    )
    assert describe_blockers(("blank_text", "low_confidence")) == "blank_text+low_confidence"


def test_policy_rejects_impossible_thresholds() -> None:
    with pytest.raises(ValueError):
        AcceptancePolicy(max_content_risk=1.5)
    with pytest.raises(ValueError):
        AcceptancePolicy(min_confidence=-0.1)
    with pytest.raises(ValueError):
        AcceptancePolicy(spot_check_risk=0.9, max_content_risk=0.2)


def test_policy_can_block_more_reasons_than_the_default() -> None:
    risk = _risk(0.45, (RiskReason.TEXT_DISAGREEMENT,), content_score=0.0)
    strict = AcceptancePolicy(blocking_reasons=frozenset({RiskReason.TEXT_DISAGREEMENT}))
    assert machine_blockers(text="子曰", risk=risk) == ()
    assert machine_blockers(text="子曰", risk=risk, policy=strict) == (
        "text_disagreement",
    )


def test_unrepresentable_characters_are_listed_once_each() -> None:
    assert unrepresentable_characters("\ufffd\ufffd\x0b") == ("U+FFFD", "U+000B")


def test_summary_distinguishes_machine_and_human_acceptance() -> None:
    summary = AcceptanceSummary(pages=1, pairs=4, machine_accepted=3, human_accepted=1)
    assert summary.accepted == 4
    assert summary.machine_acceptance == pytest.approx(0.75)
    assert not summary.requires_human_review


def test_summary_requires_review_when_a_spot_check_is_flagged() -> None:
    suspect = SuspectAcceptance(
        page_index=0,
        pair_index=1,
        actor="automatic",
        score=0.25,
        reasons=(RiskReason.LOW_CONFIDENCE,),
    )
    summary = AcceptanceSummary(pairs=1, machine_accepted=1, spot_check=(suspect,))
    assert summary.requires_human_review


def test_summary_of_nothing_is_not_empty_handed() -> None:
    summary = AcceptanceSummary()
    assert summary.machine_acceptance == 1.0
    assert not summary.requires_human_review
