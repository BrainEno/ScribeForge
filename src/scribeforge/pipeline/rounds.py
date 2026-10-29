"""Repair rounds: read the disputed lines again, and decide when to stop.

The pipeline used to have exactly one chance per pair. Two engines disagreed, the
pair joined the human queue, and the only way out was a person. That is a poor use
of both the machine and the person: a disagreement is usually a *legibility*
problem in one crop, and the cheapest fix is to read that crop again — larger,
isolated from its neighbours, and with a reader that did not produce either of the
original readings.

A round here is:

1. take every pair that still has no accepted decision (highest risk first),
2. cut the line out of the page again, with more padding and a larger scale than
   the previous attempt,
3. let each configured reader read that crop, and persist each reading as scoped
   evidence (``engine_runs.scope = "pair:<n>"``),
4. ask :func:`scribeforge.domain.consensus.consensus` whether the readings — the
   two original engine runs, the fresh re-reads, and a fresh vision-model reading
   of the same enlarged crop when one is configured — now support one text,
5. accept that text as a decision citing every supporting reading, or leave the
   pair open for the next round.

What a round can and cannot do is worth stating plainly. It **can** settle a pair
whose disagreement came from clipped or crowded pixels. It **cannot** invent
knowledge: if every reader reads the same glyph the same wrong way, another round
adds nothing but cost, and no number of rounds is a substitute for a human on a
genuinely illegible page. Rounds therefore stop on budget, when nothing is left to try, or when every
remaining pair has exhausted its attempts — and the book-level :func:`progress`
report says exactly how much was accepted without a person, so the operator can
decide whether to spot-check or escalate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scribeforge.adapters.image_crops import materialize_review_crops
from scribeforge.domain.consensus import Consensus, Reading, consensus
from scribeforge.domain.crops import plan_region_read
from scribeforge.domain.decisions import DecisionActor
from scribeforge.domain.ocr import OCREngine
from scribeforge.domain.vlm import VLMReviewer
from scribeforge.pipeline.adjudication import project_furniture, review_detail
from scribeforge.pipeline.vlm_review import ReviewEvidence
from scribeforge.storage.sqlite import SQLiteStore
from scribeforge.storage.vlm_state import SQLiteVLMReviewState

#: Prefix of the scope an engine run gets when it read one line of a page.
_REGION_SCOPE_PREFIX = "pair:"
#: Reader label used for a stored vision-model reading.
_VLM_LABEL = "vlm"
#: Labels the two whole-page readings always take.
_ENGINE_LABELS = ("primary", "secondary")
#: Labels a repair reader may not take, because round evidence is keyed by them.
RESERVED_LABELS = frozenset((*_ENGINE_LABELS, _VLM_LABEL))


def region_scope(pair_index: int) -> str:
    """Return the run scope that marks a re-read of one aligned pair."""

    if pair_index < 0:
        raise ValueError("pair index must be non-negative")
    return f"{_REGION_SCOPE_PREFIX}{pair_index}"


@dataclass(frozen=True, slots=True)
class RepairReader:
    """One way to read a disputed line again."""

    label: str
    engine: OCREngine

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("a repair reader needs a label")
        if self.label in RESERVED_LABELS:
            raise ValueError(f"reader label {self.label!r} is reserved")


def reader_for(engine: OCREngine) -> RepairReader:
    """Wrap an engine as a repair reader with a label that cannot collide.

    The ``:reread`` suffix keeps the label distinct from the whole-page readings
    called ``primary``/``secondary`` and from the vision model's ``vlm``, even when
    the engine is literally named ``primary`` (which the test doubles are).
    """

    return RepairReader(f"{engine.name}:reread", engine)


@dataclass(frozen=True, slots=True)
class RepairSettings:
    """How hard, and how expensively, the repair stage may try."""

    max_rounds: int = 3
    max_pairs_per_round: int = 40
    max_attempts_per_pair: int = 3
    base_padding: float = 0.015
    padding_step: float = 0.02
    max_padding: float = 0.08
    max_scale: int = 3

    def __post_init__(self) -> None:
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        if self.max_pairs_per_round < 1:
            raise ValueError("max_pairs_per_round must be at least 1")
        if self.max_attempts_per_pair < 1:
            raise ValueError("max_attempts_per_pair must be at least 1")
        if self.base_padding < 0 or self.padding_step < 0:
            raise ValueError("crop padding must be non-negative")
        if self.max_padding < self.base_padding:
            raise ValueError("max_padding must not be smaller than base_padding")
        if self.max_scale < 1:
            raise ValueError("max_scale must be at least 1")

    def escalation(self, attempts: int) -> tuple[float, int]:
        """Return the padding and scale to use for the next attempt.

        Each attempt widens the context around the line and enlarges the pixels.
        The point is to change the *input*: repeating an identical crop with the
        same engine can only reproduce the same text.
        """

        if attempts < 0:
            raise ValueError("attempts must be non-negative")
        padding = min(self.max_padding, self.base_padding + self.padding_step * attempts)
        scale = min(self.max_scale, 1 + attempts)
        return padding, scale


@dataclass(frozen=True, slots=True)
class PairRef:
    """One aligned pair, addressed the way the store addresses it."""

    page_index: int
    pair_index: int
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class RoundReport:
    """What one repair round did, pair by pair."""

    round_index: int
    examined: tuple[PairRef, ...] = ()
    resolved: tuple[PairRef, ...] = ()
    still_open: tuple[PairRef, ...] = ()
    skipped: tuple[PairRef, ...] = ()
    deferred: tuple[PairRef, ...] = ()

    @property
    def progressed(self) -> bool:
        return bool(self.resolved)

    @property
    def remaining(self) -> tuple[PairRef, ...]:
        """Pairs this round could not settle, for the operator's summary."""

        return self.still_open + self.skipped + self.deferred


@dataclass(frozen=True, slots=True)
class ProgressReport:
    """How much of the book was accepted without a person.

    This is the number an operator should look at before deciding to run another
    round, spot-check, or read line by line.
    """

    pages: int
    pairs: int
    accepted_automatic: int
    accepted_vlm: int
    accepted_human: int
    open_pairs: int
    open_high_risk: int

    @property
    def accepted(self) -> int:
        return self.accepted_automatic + self.accepted_vlm + self.accepted_human

    @property
    def needs_review(self) -> bool:
        return self.open_pairs > 0

    @property
    def auto_acceptance(self) -> float:
        """Fraction of content pairs the pipeline settled on its own."""

        if not self.pairs:
            return 1.0
        return (self.accepted_automatic + self.accepted_vlm) / self.pairs


def open_pairs(store: SQLiteStore, project_id: int) -> tuple[PairRef, ...]:
    """List undecided, non-furniture pairs, highest risk first."""

    furniture = project_furniture(store, project_id)
    refs: list[PairRef] = []
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            continue
        record = store.load_verification(verification_id)
        page_furniture = furniture.get(page.page_index, {})
        for pair_index, pair in enumerate(record.alignment.pairs):
            if any(
                index is not None and index in page_furniture
                for index in (pair.primary_line_index, pair.secondary_line_index)
            ):
                continue
            if store.latest_decision(project_id, page.page_index, pair_index) is None:
                refs.append(
                    PairRef(page.page_index, pair_index, record.risks[pair_index].score)
                )
    refs.sort(key=lambda ref: (-ref.score, ref.page_index, ref.pair_index))
    return tuple(refs)


def _engine_readings(
    store: SQLiteStore, project_id: int, page_index: int, pair_index: int
) -> tuple[list[Reading], dict[str, tuple[str, ...]]]:
    """Collect the two whole-page engine readings of one pair, with evidence refs.

    Only persisted readings are used. A decision may not cite a reading that was
    never stored, or the accepted text would lose its trail back to evidence.

    A stored *vision-model* reading is deliberately not reused here: a confident
    one has already been accepted as the pair's decision (so the pair is no longer
    open), and an uncertain or blank one may not vote. The vision model earns its
    place in a round by reading the enlarged crop again, not by repeating itself.
    """

    verification_id = store.latest_verification_id(project_id, page_index)
    record = store.load_verification(verification_id)
    pair = record.alignment.pairs[pair_index]
    primary = store.load_ocr_run(record.primary_run_id)
    secondary = store.load_ocr_run(record.secondary_run_id)

    readings: list[Reading] = []
    refs: dict[str, tuple[str, ...]] = {}
    for label, result, index, run_id in (
        ("primary", primary, pair.primary_line_index, record.primary_run_id),
        ("secondary", secondary, pair.secondary_line_index, record.secondary_run_id),
    ):
        text = None if index is None else result.lines[index].text
        readings.append(Reading(label, text))
        refs[label] = (f"engine_run:{run_id}",)

    return readings, refs


def _vision_reading(
    store: SQLiteStore,
    reviewer: VLMReviewer,
    *,
    verification_id: int,
    page_index: int,
    pair_index: int,
    crop_path: str,
    has_candidate: bool,
) -> tuple[Reading, str] | None:
    """Ask the vision model to read the enlarged crop, and persist its answer.

    Returns the reading and the citation of the persisted row, so the decision that
    uses it can point at evidence rather than at "a model said so".
    """

    if not has_candidate:
        # The store can only attach a VLM reading to a persisted review
        # candidate; a model answer that cannot be stored must not be cited.
        return None
    state = SQLiteVLMReviewState(store, verification_id)
    reading = reviewer.transcribe_crop(
        crop_path, page_index=page_index, pair_index=pair_index
    )
    if reading.page_index != page_index or reading.pair_index != pair_index:
        raise ValueError("VLM reading identity does not match the pair being repaired")
    state.append(ReviewEvidence(reading=reading, crop_path=crop_path))
    stored = [
        review
        for review in store.list_vlm_reviews(verification_id)
        if review.pair_index == pair_index
    ]
    citation = f"vlm_review:{stored[-1].id}" if stored else "vlm_review:unknown"
    return Reading(_VLM_LABEL, reading.text, uncertain=reading.uncertain), citation


def run_repair_round(
    store: SQLiteStore,
    project_id: int,
    *,
    readers: Sequence[RepairReader],
    crops_dir: Path,
    reviewer: VLMReviewer | None = None,
    settings: RepairSettings | None = None,
    round_index: int = 1,
) -> RoundReport:
    """Re-read the still-undecided pairs and accept whatever the readings agree on."""

    if not readers:
        raise ValueError("a repair round needs at least one reader")
    labels = [reader.label for reader in readers]
    if len(set(labels)) != len(labels):
        raise ValueError("repair readers must have distinct labels")
    settings = settings or RepairSettings()

    pages = {page.page_index: page for page in store.list_pages(project_id)}
    undecided = open_pairs(store, project_id)
    candidates = undecided[: settings.max_pairs_per_round]
    deferred = undecided[len(candidates) :]

    examined: list[PairRef] = []
    resolved: list[PairRef] = []
    still_open: list[PairRef] = []
    skipped: list[PairRef] = []

    for ref in candidates:
        page = pages.get(ref.page_index)
        if page is None:
            skipped.append(ref)
            continue
        scope = region_scope(ref.pair_index)
        attempts = len(store.list_scoped_run_ids(project_id, ref.page_index, scope))
        if attempts >= settings.max_attempts_per_pair:
            # Every escalation has been tried. Another identical attempt would
            # produce another identical reading and hide the pair from the person
            # who has to decide it.
            skipped.append(ref)
            continue

        detail = review_detail(store, project_id, ref.page_index, ref.pair_index)
        if detail.source_box is None:
            skipped.append(ref)
            continue

        padding, scale = settings.escalation(attempts)
        plan = plan_region_read(
            page_index=ref.page_index,
            pair_index=ref.pair_index,
            box=detail.source_box,
            page_width=page.width,
            page_height=page.height,
            attempt=attempts + 1,
            padding_fraction=padding,
            scale=scale,
        )
        crop_path = materialize_review_crops(
            Path(page.source_image), (plan,), crops_dir
        )[ref.pair_index]
        examined.append(ref)

        verification_id = store.latest_verification_id(project_id, ref.page_index)
        readings, refs = _engine_readings(
            store, project_id, ref.page_index, ref.pair_index
        )
        order = [reading.reader for reading in readings]
        for reader in readers:
            result = reader.engine.analyze_page(crop_path, ref.page_index)
            run_id = store.record_ocr(project_id, result, scope=scope)
            # A pair is one printed line. A reader that splits it into several
            # lines still returns the same characters, so the text is the
            # concatenation rather than whichever line happened to look longest.
            readings.append(Reading(reader.label, "".join(line.text for line in result.lines)))
            refs[reader.label] = (f"engine_run:{run_id}", f"region_read:{scope}")
            order.append(reader.label)

        if reviewer is not None:
            record = store.load_verification(verification_id)
            vision = _vision_reading(
                store,
                reviewer,
                verification_id=verification_id,
                page_index=ref.page_index,
                pair_index=ref.pair_index,
                crop_path=crop_path,
                has_candidate=any(
                    candidate.pair_index == ref.pair_index
                    for candidate in record.candidates
                ),
            )
            if vision is not None:
                vision_reading, vision_citation = vision
                readings.append(vision_reading)
                order.append(_VLM_LABEL)
                refs[_VLM_LABEL] = (
                    f"verification_run:{verification_id}",
                    vision_citation,
                )

        verdict: Consensus = consensus(readings, prefer=order)
        if not verdict.settled or verdict.text is None:
            still_open.append(ref)
            continue

        evidence: list[str] = [f"verification_run:{verification_id}"]
        for supporter in verdict.supporters:
            for citation in refs.get(supporter, ()):
                if citation not in evidence:
                    evidence.append(citation)
        evidence.append(f"consensus:{verdict.rationale}")
        actor = (
            DecisionActor.VLM
            if _VLM_LABEL in verdict.supporters
            else DecisionActor.AUTOMATIC
        )
        store.append_decision(
            project_id=project_id,
            page_index=ref.page_index,
            pair_index=ref.pair_index,
            selected_text=verdict.text,
            actor=actor.value,
            evidence_refs=tuple(evidence),
        )
        resolved.append(ref)

    return RoundReport(
        round_index=round_index,
        examined=tuple(examined),
        resolved=tuple(resolved),
        still_open=tuple(still_open),
        skipped=tuple(skipped),
        deferred=tuple(deferred),
    )


def repair_book(
    store: SQLiteStore,
    project_id: int,
    *,
    readers: Sequence[RepairReader],
    crops_dir: Path,
    reviewer: VLMReviewer | None = None,
    settings: RepairSettings | None = None,
) -> tuple[RoundReport, ...]:
    """Run rounds until the book is settled, stuck, or out of budget."""

    settings = settings or RepairSettings()
    reports: list[RoundReport] = []
    for round_index in range(1, settings.max_rounds + 1):
        report = run_repair_round(
            store,
            project_id,
            readers=readers,
            crops_dir=crops_dir,
            reviewer=reviewer,
            settings=settings,
            round_index=round_index,
        )
        if not report.examined and not report.skipped:
            # Nothing left to read and nothing left to report: the book is
            # settled, or has no pairs at all. An empty round is not evidence.
            break
        reports.append(report)
        if not report.examined:
            # Every remaining pair has exhausted its attempts; they need a person
            # now, and the report that says so is the last one.
            break
    return tuple(reports)


def progress(
    store: SQLiteStore, project_id: int, *, high_risk: float = 0.6
) -> ProgressReport:
    """Summarise how much of the book was accepted without a person."""

    undecided = open_pairs(store, project_id)
    actors = store.latest_decision_actors(project_id)
    automatic = sum(
        1 for actor in actors.values() if actor == DecisionActor.AUTOMATIC.value
    )
    vlm = sum(1 for actor in actors.values() if actor == DecisionActor.VLM.value)
    human = sum(1 for actor in actors.values() if actor == DecisionActor.HUMAN.value)
    return ProgressReport(
        pages=len(store.list_pages(project_id)),
        pairs=len(actors) + len(undecided),
        accepted_automatic=automatic,
        accepted_vlm=vlm,
        accepted_human=human,
        open_pairs=len(undecided),
        open_high_risk=sum(1 for ref in undecided if ref.score >= high_risk),
    )
