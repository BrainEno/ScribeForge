from __future__ import annotations

from collections.abc import Sequence
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

    Thin wrapper over :func:`adjudicate_readings` kept for the two-engine call
    sites; the N-engine rule below is the single source of truth.
    """

    return adjudicate_readings([primary_text, secondary_text], vlm, risk=risk, policy=policy)


def adjudicate_readings(
    engine_texts: Sequence[str | None],
    vlm: VLMReading | None,
    *,
    risk: PairRisk | None = None,
    policy: AcceptancePolicy = DEFAULT_POLICY,
) -> PairAdjudication:
    """Adjudicate one aligned line across *N* engines (issue #90).

    Pure rule, no I/O:

    - All engines that produced text agree exactly → accept (``engines_agree``).
    - All produced texts differ only by glyph variant → accept the *first*
      engine's literal characters (``variant_agreement``); source OCR text is
      never folded or "corrected".
    - Otherwise, cluster texts by variant equivalence; a cluster held by at
      least two engines wins (``majority_agreement``) and keeps the first
      literal text of that cluster. With three engines this is 2-of-3; with two
      it degenerates to the historical ``variant_agreement``/``engines_agree``.
    - A confident, non-blank VLM reading corroborating *any* engine (exact or
      variant-only) is accepted as the VLM actor (``vlm_confident``). A VLM
      reading that matches nothing is a third opinion, not a corroboration
      (``vlm_uncorroborated``); uncertain/blank VLM never resolves.
    - Everything else — including three-way disagreement — stays queued for a
      human (``engines_disagree`` / ``missing_engine_text`` / ``no_engine_text``).

    Whatever a rule accepts still passes the acceptance gate unchanged.
    """

    present = [text for text in engine_texts if text is not None]
    if not present:
        return PairAdjudication(None, None, "no_engine_text")

    agreement = _engine_agreement(present)
    if agreement is not None:
        text, rationale = agreement
        return _gated(text, DecisionActor.AUTOMATIC, rationale, risk, policy)

    if vlm is None:
        if len(present) < 2:
            return PairAdjudication(None, None, "missing_engine_text")
        return PairAdjudication(None, None, "engines_disagree")

    if vlm.uncertain:
        return PairAdjudication(None, None, "vlm_uncertain")
    if not vlm.text.strip():
        return PairAdjudication(None, None, "vlm_blank")
    if not any(vlm.text == text or is_variant_only_difference(vlm.text, text) for text in present):
        # A reading that matches no engine is the *most* suspicious case: there is
        # no independent agreement to accept, only competing claims. It goes to a
        # person.
        return PairAdjudication(None, None, "vlm_uncorroborated")
    return _gated(vlm.text, DecisionActor.VLM, "vlm_confident", risk, policy)


def _engine_agreement(present: list[str]) -> tuple[str, str] | None:
    """Return ``(text, rationale)`` when the engines' texts form an accept cluster.

    Exact-equal texts group trivially; otherwise texts that differ only by glyph
    variant cluster together. The first literal text of the winning group is
    kept verbatim. ``None`` when no group reaches agreement (fewer than two
    engines in every cluster).
    """

    groups: list[list[int]] = []
    for index, text in enumerate(present):
        for group in groups:
            representative = present[group[0]]
            if text == representative or is_variant_only_difference(text, representative):
                group.append(index)
                break
        else:
            groups.append([index])
    if not groups:
        return None
    best = max(groups, key=len)
    if len(best) < 2:
        return None
    first = present[best[0]]
    if len(best) == len(present):
        # Every engine sits in the same cluster: exact agreement when all are
        # identical, variant-only agreement otherwise.
        if all(text == first for text in present):
            return first, "engines_agree"
        return first, "variant_agreement"
    return first, "majority_agreement"


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
