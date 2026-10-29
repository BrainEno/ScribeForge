from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from scribeforge.domain.acceptance import (
    DEFAULT_POLICY,
    AcceptancePolicy,
    describe_blockers,
    machine_blockers,
)
from scribeforge.domain.normalize import is_variant_only_difference
from scribeforge.domain.risk import PairRisk
from scribeforge.domain.vlm import VLMReading


class DecisionActor(str, Enum):
    AUTOMATIC = "automatic"
    VLM = "vlm"
    HUMAN = "human"


@dataclass(frozen=True, slots=True)
class PairAdjudication:
    """Deterministic outcome for one aligned pair.

    ``selected_text`` is ``None`` when the pair cannot be accepted from evidence
    alone and must stay queued for human review.
    """

    selected_text: str | None
    actor: DecisionActor | None
    rationale: str
    #: Why the acceptance gate refused this pair even though a rule produced text.
    #: Empty when the pair was accepted or when no rule produced text at all.
    blockers: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.selected_text is not None

    @property
    def blocked(self) -> bool:
        """True when a rule accepted the text but the gate refused it."""

        return bool(self.blockers)

    @property
    def blocked_reason(self) -> str:
        return describe_blockers(self.blockers)


def adjudicate_pair(
    primary_text: str | None,
    secondary_text: str | None,
    vlm: VLMReading | None,
    *,
    risk: PairRisk | None = None,
    policy: AcceptancePolicy = DEFAULT_POLICY,
) -> PairAdjudication:
    """Turn a verified pair into an accepted-text decision from cited evidence.

    Independent agreement is accepted automatically, including agreement that
    differs only by glyph variant (the primary engine's characters are kept
    verbatim). A confident, non-blank VLM reading is accepted only when one of the
    engines reads the same text: a vision model that disagrees with *both* engines
    is a third opinion, not a corroboration, and self-reported confidence is not
    evidence (issue #76). Everything else stays unresolved for human review.
    Source OCR text is never rewritten by this rule.
    """

    if primary_text is None and secondary_text is None:
        return PairAdjudication(None, None, "no_engine_text")

    if primary_text is not None and primary_text == secondary_text:
        return _gated(
            primary_text, DecisionActor.AUTOMATIC, "engines_agree", risk, policy
        )

    if (
        primary_text is not None
        and secondary_text is not None
        and is_variant_only_difference(primary_text, secondary_text)
    ):
        # The engines agree on the text modulo orthography. Accept the primary
        # engine's literal characters — never a folded or "corrected" form — and
        # record why, so the choice is reviewable rather than silent.
        return _gated(
            primary_text, DecisionActor.AUTOMATIC, "variant_agreement", risk, policy
        )

    if vlm is None:
        if primary_text is None or secondary_text is None:
            return PairAdjudication(None, None, "missing_engine_text")
        return PairAdjudication(None, None, "engines_disagree")

    if vlm.uncertain:
        return PairAdjudication(None, None, "vlm_uncertain")
    if not vlm.text.strip():
        return PairAdjudication(None, None, "vlm_blank")
    engine_texts = [text for text in (primary_text, secondary_text) if text is not None]
    if not any(
        vlm.text == text or is_variant_only_difference(vlm.text, text)
        for text in engine_texts
    ):
        # A third reading that matches nothing is the *most* suspicious case: two
        # engines and a model all disagree, so there is no independent agreement to
        # accept, only three claims. It goes to a person.
        return PairAdjudication(None, None, "vlm_uncorroborated")
    return _gated(vlm.text, DecisionActor.VLM, "vlm_confident", risk, policy)


def _gated(
    text: str,
    actor: DecisionActor,
    rationale: str,
    risk: PairRisk | None,
    policy: AcceptancePolicy,
) -> PairAdjudication:
    """Accept ``text`` unless the evidence gate says a person must decide.

    Agreement and VLM corroboration answer "do independent readers agree?"; the
    gate answers a second, independent question: "is this text fit to accept
    without a person?". A pair that fails the second question stays unresolved
    even when the first says yes — a replacement character both engines produced
    is still not text (issue #75).
    """

    blockers = machine_blockers(text=text, risk=risk, policy=policy)
    if blockers:
        return PairAdjudication(None, None, rationale, blockers=blockers)
    return PairAdjudication(text, actor, rationale)
