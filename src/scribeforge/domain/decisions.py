from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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

    @property
    def resolved(self) -> bool:
        return self.selected_text is not None


def adjudicate_pair(
    primary_text: str | None,
    secondary_text: str | None,
    vlm: VLMReading | None,
) -> PairAdjudication:
    """Turn a verified pair into an accepted-text decision from cited evidence.

    Independent agreement is accepted automatically. Otherwise a confident,
    non-blank VLM reading may be accepted. Everything else stays unresolved for
    human review. Source OCR text is never rewritten by this rule.
    """

    if primary_text is None and secondary_text is None:
        return PairAdjudication(None, None, "no_engine_text")

    if primary_text is not None and primary_text == secondary_text:
        return PairAdjudication(primary_text, DecisionActor.AUTOMATIC, "engines_agree")

    if vlm is None:
        if primary_text is None or secondary_text is None:
            return PairAdjudication(None, None, "missing_engine_text")
        return PairAdjudication(None, None, "engines_disagree")

    if vlm.uncertain:
        return PairAdjudication(None, None, "vlm_uncertain")
    if not vlm.text.strip():
        return PairAdjudication(None, None, "vlm_blank")
    return PairAdjudication(vlm.text, DecisionActor.VLM, "vlm_confident")
