"""May a machine accept this text, or does a person have to look at it?

Adjudication answers *one* question: do the readers agree? That is not the same as
"is this safe to export". Two engines can agree on a replacement character
(``U+FFFD``), on text they both read at 10% confidence, or on a line whose two
boxes sit far apart — the first two mean the text is not even readable, and the
third means the pair may not be the same line at all. Before this module existed,
agreement alone accepted all three and removed them from the review queue, so the
defect reached the exported book (issue #75).

The gate here is deliberately *narrow*: it only judges the accepted text and the
risk the page already reported. It never picks a winner, never rewrites text, and
never blocks a human — a person may accept anything, and their decision cites the
same evidence as any other. What it blocks is the **machine** accepting content
that no machine should be able to certify.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from scribeforge.domain.risk import PairRisk, RiskReason

#: Characters XML 1.0 cannot carry, plus the replacement character an engine uses
#: when it could not read a glyph. A book that contains one of these is not a book
#: whose text can be trusted, whatever the other reader said.
_REPLACEMENT_CHARACTER = "\ufffd"
_ALLOWED_CONTROL = frozenset("\t\n\r")


def has_unrepresentable_characters(text: str) -> bool:
    """True when ``text`` contains a character no export can honestly carry."""

    return bool(unrepresentable_characters(text))


def unrepresentable_characters(text: str) -> tuple[str, ...]:
    """Name the offending characters (``U+000B``), first occurrence order.

    Naming them matters for the operator: "this line is blocked" is not actionable,
    "this line contains U+FFFD, the engine could not read the glyph" is.
    """

    found: list[str] = []
    for character in text:
        if character in _ALLOWED_CONTROL:
            continue
        if character == _REPLACEMENT_CHARACTER or unicodedata.category(
            character
        ).startswith("C"):
            name = f"U+{ord(character):04X}"
            if name not in found:
                found.append(name)
    return tuple(found)


@dataclass(frozen=True, slots=True)
class AcceptancePolicy:
    """Thresholds that decide what a machine may accept without a person.

    ``max_content_risk`` caps the part of the risk score that comes from *evidence
    quality* — low confidence, unicode anomalies, misaligned boxes — and ignores the
    part that comes from structural disagreement. That split matters: "the engines
    disagreed and a vision model settled it" is a resolved question, so folding it
    into the number the gate reads would block every vision-model decision and
    silently disable the feature.

    ``min_confidence`` is a floor on the worst reader's confidence. An engine that
    reports 0.1 has told us it is guessing; two engines guessing the same way is
    not corroboration.
    """

    max_content_risk: float = 0.34
    min_confidence: float = 0.30
    #: Reason codes that always block machine acceptance, whatever the score.
    #: A unicode anomaly means the text is not text; a spatial mismatch means the
    #: two boxes may not even be the same line.
    blocking_reasons: frozenset[RiskReason] = field(
        default_factory=lambda: frozenset(
            {RiskReason.UNICODE_ANOMALY, RiskReason.SPATIAL_MISMATCH}
        )
    )
    #: Machine-accepted pairs at or above this content risk are worth a spot-check.
    spot_check_risk: float = 0.25

    def __post_init__(self) -> None:
        if not 0.0 <= self.max_content_risk <= 1.0:
            raise ValueError("max_content_risk must be between 0 and 1")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if not 0.0 <= self.spot_check_risk <= 1.0:
            raise ValueError("spot_check_risk must be between 0 and 1")
        if self.spot_check_risk > self.max_content_risk:
            raise ValueError(
                "spot_check_risk must not exceed max_content_risk: a machine-accepted "
                "risk above the ceiling could never be accepted, so it could never "
                "be checked"
            )


#: The policy used unless a caller passes its own.
DEFAULT_POLICY = AcceptancePolicy()


def machine_blockers(
    *,
    text: str,
    risk: PairRisk | None,
    policy: AcceptancePolicy = DEFAULT_POLICY,
) -> tuple[str, ...]:
    """Return why a machine may not accept ``text`` (empty when it may).

    Order is stable — text defects first, then evidence quality — so a rationale
    string is reproducible and can be asserted on.
    """

    blockers: list[str] = []
    if not text.strip():
        blockers.append("blank_text")
    if has_unrepresentable_characters(text):
        blockers.append("unrepresentable_text")
    if risk is None:
        return tuple(blockers)
    # Report the blocking reasons in the enum's own order rather than in whatever
    # order the risk assessment happened to store them: a rationale that reorders
    # itself between runs is not reproducible, and reproducibility is the whole
    # point of writing the rationale down.
    present = set(risk.reasons)
    for reason in RiskReason:
        if reason in policy.blocking_reasons and reason in present:
            name = reason.value
            if name not in blockers:
                blockers.append(name)
    if risk.content_score > policy.max_content_risk:
        blockers.append("high_content_risk")
    if (
        risk.min_confidence is not None
        and risk.min_confidence < policy.min_confidence
        and "low_confidence" not in blockers
    ):
        blockers.append("low_confidence")
    return tuple(blockers)


def describe_blockers(blockers: tuple[str, ...]) -> str:
    """Human-readable rationale for a blocked acceptance."""

    return "+".join(blockers)


@dataclass(frozen=True, slots=True)
class SuspectAcceptance:
    """An accepted pair a person may want to look at anyway."""

    page_index: int
    pair_index: int
    actor: str
    score: float
    reasons: tuple[RiskReason, ...]
    #: Free-text remark, e.g. that the accepted text itself is not representable.
    note: str = ""


@dataclass(frozen=True, slots=True)
class AcceptanceSummary:
    """How much of the accepted text a machine certified, and how risky that was.

    Export reports this because "verified" is not one thing: ``automatic`` and
    ``vlm`` decisions were accepted by the pipeline, ``human`` decisions by a
    person. An operator who wants line-by-line certainty can require
    ``requires_human_review`` to be false before shipping the book.
    """

    pages: int = 0
    pairs: int = 0
    machine_accepted: int = 0
    human_accepted: int = 0
    max_machine_risk: float = 0.0
    spot_check: tuple[SuspectAcceptance, ...] = ()

    @property
    def accepted(self) -> int:
        return self.machine_accepted + self.human_accepted

    @property
    def requires_human_review(self) -> bool:
        """True when a machine accepted something a person has not looked at."""

        return bool(self.spot_check)

    @property
    def machine_acceptance(self) -> float:
        if not self.accepted:
            return 1.0
        return self.machine_accepted / self.accepted
