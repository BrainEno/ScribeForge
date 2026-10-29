"""Decide whether independent readings of the same line actually agree.

ScribeForge already rejects a pair when its two engines disagree. What it could
not do was *add* evidence: a disputed line stayed in the human queue no matter how
many more times it was read. Re-reading is only useful if the result can be
turned into an accepted decision, and that needs a rule about how much agreement
is enough.

The rule here is deliberately conservative and deliberately simple:

* A reading that is blank or self-declared uncertain never supports anything.
  Absence of text is not a vote for the empty string.
* Text that differs only by glyph variant counts as the *same* reading (folded
  comparison), but the accepted characters are always one reader's literal
  characters — never a folded or "corrected" form.
* A text needs **two independent readers** to be accepted. One reader, however
  confident, is not a consensus: that is the defect behind issue #76, where a
  vision model's self-reported confidence was enough on its own.
* A tie between two groups of two or more never resolves. Adding readers must
  reduce ambiguity, not manufacture a winner out of it.

Everything here is pure: readings in, verdict out, no store and no model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from scribeforge.domain.normalize import is_variant_only_difference

#: How many independent readers must support a text before it can be accepted.
MIN_SUPPORTERS = 2


@dataclass(frozen=True, slots=True)
class Reading:
    """One independent reading of the same passage."""

    reader: str
    text: str | None
    #: A reader may report text *and* say it is unsure of it. Such a reading is
    #: kept for the record but counts for neither side of the argument.
    uncertain: bool = False

    def __post_init__(self) -> None:
        if not self.reader.strip():
            raise ValueError("a reading must name its reader")

    @property
    def usable(self) -> bool:
        """True when this reading may vote."""

        return not self.uncertain and bool(self.text and self.text.strip())


@dataclass(frozen=True, slots=True)
class Consensus:
    """The verdict for one passage, with the readers that produced it."""

    text: str | None
    supporters: tuple[str, ...] = ()
    considered: tuple[str, ...] = ()
    rationale: str = "no_reading"

    @property
    def settled(self) -> bool:
        return self.text is not None


def _same_reading(left: str, right: str) -> bool:
    """True when two texts are the same reading modulo orthography."""

    return left == right or is_variant_only_difference(left, right)


def _groups(usable: Sequence[Reading]) -> list[list[Reading]]:
    """Partition readings into agreement groups, keeping input order."""

    groups: list[list[Reading]] = []
    for reading in usable:
        assert reading.text is not None
        for group in groups:
            representative = group[0].text
            assert representative is not None
            if _same_reading(representative, reading.text):
                group.append(reading)
                break
        else:
            groups.append([reading])
    return groups


def _choose_text(group: Sequence[Reading], prefer: Sequence[str]) -> str:
    """Pick the literal text to accept from an agreement group.

    Preference order, so a caller can say which engine's orthography the book
    should follow: an exact duplicate of the most common literal text wins, then
    the earliest reader in ``prefer``, then the first reader that spoke.
    """

    counts: dict[str, int] = {}
    for reading in group:
        assert reading.text is not None
        counts[reading.text] = counts.get(reading.text, 0) + 1
    candidates = list(counts)
    order = {reader: index for index, reader in enumerate(prefer)}
    return min(
        candidates,
        key=lambda text: (
            -counts[text],
            min(
                order.get(reading.reader, len(order))
                for reading in group
                if reading.text == text
            ),
        ),
    )


def consensus(
    readings: Sequence[Reading], *, prefer: Sequence[str] = ()
) -> Consensus:
    """Return the agreed text, or an unsettled verdict explaining why not.

    ``prefer`` orders readers for the case where an agreement group contains more
    than one literal text (glyph variants): the winner's spelling then follows the
    caller's engine order instead of whichever reader happened to run last.
    """

    usable = [reading for reading in readings if reading.usable]
    considered = tuple(reading.reader for reading in usable)
    if not usable:
        return Consensus(None, (), (), "no_reading")

    groups = _groups(usable)
    ranked = sorted(groups, key=len, reverse=True)
    best = ranked[0]
    if len(best) < MIN_SUPPORTERS:
        return Consensus(None, (), considered, "single_reader")
    if len(ranked) > 1 and len(ranked[1]) == len(best):
        return Consensus(None, (), considered, "tie")
    text = _choose_text(best, prefer)
    supporters = tuple(reading.reader for reading in best)
    rationale = "unanimous" if len(groups) == 1 else "majority"
    return Consensus(text, supporters, considered, rationale)
