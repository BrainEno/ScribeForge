"""Exhaustive truth-table tests for the N-engine adjudication rule (#90).

`adjudicate_readings` is a pure function over (engine texts, VLM reading,
risk, policy). These tests enumerate the full product space of a small
alphabet — an exact-agreement symbol, a glyph-variant pair, a disagreeing
symbol and a missing reading — across three engines and six VLM states, and
check every cell against an independent expectation model written from the
spec (not from the implementation). `summarize_engine_votes` is the pure
vote-distribution helper the pipeline/report layers use.
"""

from __future__ import annotations

import itertools

from scribeforge.domain.decisions import (
    DecisionActor,
    adjudicate_readings,
    summarize_engine_votes,
)
from scribeforge.domain.normalize import is_variant_only_difference
from scribeforge.domain.vlm import VLMReading

# A and A_VARIANT are a real glyph-variant pair; B disagrees with both.
A = "鸦"
A_VARIANT = "鴉"
B = "雅"
C = "書"
assert is_variant_only_difference(A, A_VARIANT)
assert not is_variant_only_difference(A, B)

ALPHABET = (A, A_VARIANT, B, None)

VLM_STATES: tuple[tuple[str, VLMReading | None], ...] = (
    ("none", None),
    ("confident-A", VLMReading(0, 0, "m", "1", A, False)),
    ("confident-A_variant", VLMReading(0, 0, "m", "1", A_VARIANT, False)),
    ("confident-B", VLMReading(0, 0, "m", "1", B, False)),
    ("confident-novel-C", VLMReading(0, 0, "m", "1", C, False)),
    ("uncertain", VLMReading(0, 0, "m", "1", A, True)),
    ("blank", VLMReading(0, 0, "m", "1", "   ", False)),
)


def _expected(engine_texts, vlm):
    """Independent expectation model written from the rule's specification."""
    present = [t for t in engine_texts if t is not None]
    if not present:
        return (False, None, "no_engine_text")
    # Cluster by variant equivalence.
    clusters: list[list[str]] = []
    for text in present:
        for cluster in clusters:
            if text == cluster[0] or is_variant_only_difference(text, cluster[0]):
                cluster.append(text)
                break
        else:
            clusters.append([text])
    winner = max(clusters, key=len)
    if len(winner) >= 2:
        if len(winner) == len(present):
            rationale = "engines_agree" if len(set(present)) == 1 else "variant_agreement"
        else:
            rationale = "majority_agreement"
        return (True, DecisionActor.AUTOMATIC, rationale)
    if vlm is None:
        if len(present) < 2:
            return (False, None, "missing_engine_text")
        return (False, None, "engines_disagree")
    if vlm.uncertain:
        return (False, None, "vlm_uncertain")
    if not vlm.text.strip():
        return (False, None, "vlm_blank")
    if not any(vlm.text == t or is_variant_only_difference(vlm.text, t) for t in present):
        return (False, None, "vlm_uncorroborated")
    return (True, DecisionActor.VLM, "vlm_confident")


def test_truth_table_exhaustive_over_three_engines_and_vlm_states() -> None:
    checked = 0
    for engine_texts in itertools.product(ALPHABET, repeat=3):
        for state_name, vlm in VLM_STATES:
            result = adjudicate_readings(list(engine_texts), vlm)
            expected = _expected(engine_texts, vlm)
            assert result.resolved == expected[0], (engine_texts, state_name)
            assert result.actor == expected[1], (engine_texts, state_name)
            assert result.rationale == expected[2], (engine_texts, state_name)
            if result.resolved and result.actor is DecisionActor.AUTOMATIC:
                # The accepted text is always the first literal of the winning cluster.
                assert result.selected_text in engine_texts
            checked += 1
    assert checked == 4**3 * len(VLM_STATES) == 448


def test_truth_table_two_engines_degenerates_to_historical_rule() -> None:
    for engine_texts in itertools.product(ALPHABET, repeat=2):
        for _state, vlm in VLM_STATES:
            result = adjudicate_readings(list(engine_texts), vlm)
            expected = _expected(engine_texts, vlm)
            assert (result.resolved, result.actor, result.rationale) == expected


# ---------------------------------------------------------------------------
# summarize_engine_votes: pure vote-distribution helper
# ---------------------------------------------------------------------------


def test_vote_summary_counts_clusters_and_winner() -> None:
    summary = summarize_engine_votes([A, A_VARIANT, B])

    assert summary.engine_count == 3
    assert summary.text_count == 3
    assert summary.cluster_sizes == (2, 1)
    assert summary.winner_size == 2
    assert summary.unanimous is False
    assert summary.majority is True
    assert summary.split is False


def test_vote_summary_unanimous_exact_and_variant() -> None:
    exact = summarize_engine_votes([A, A, A])
    assert exact.unanimous is True and exact.majority is True and exact.split is False
    assert exact.cluster_sizes == (3,)

    variant = summarize_engine_votes([A, A_VARIANT, A])
    assert variant.unanimous is True  # one variant cluster holds everyone
    assert variant.cluster_sizes == (3,)


def test_vote_summary_three_way_split() -> None:
    summary = summarize_engine_votes([A, B, C])

    assert summary.cluster_sizes == (1, 1, 1)
    assert summary.winner_size == 1
    assert summary.majority is False
    assert summary.split is True
    assert summary.unanimous is False


def test_vote_summary_ignores_missing_readings_but_counts_them() -> None:
    summary = summarize_engine_votes([A, None, A])

    assert summary.engine_count == 3
    assert summary.text_count == 2
    assert summary.cluster_sizes == (2,)
    assert summary.majority is True

    empty = summarize_engine_votes([None, None])
    assert empty.engine_count == 2
    assert empty.text_count == 0
    assert empty.cluster_sizes == ()
    assert empty.majority is False and empty.split is False and empty.unanimous is False


def test_adjudication_carries_vote_summary() -> None:
    result = adjudicate_readings([A, B, A], None)

    assert result.votes is not None
    assert result.votes.cluster_sizes == (2, 1)
    assert result.votes.winner_size == 2


def test_truth_table_exhaustive_over_four_engines() -> None:
    """The rule is N-ary: the same expectation model must hold for 4 engines."""
    checked = 0
    for engine_texts in itertools.product(ALPHABET, repeat=4):
        for state_name, vlm in VLM_STATES:
            result = adjudicate_readings(list(engine_texts), vlm)
            expected = _expected(engine_texts, vlm)
            assert (result.resolved, result.actor, result.rationale) == expected, (
                engine_texts,
                state_name,
            )
            checked += 1
    assert checked == 4**4 * len(VLM_STATES) == 1792


def test_majority_of_four_wins_and_three_way_split_stays_queued() -> None:
    result = adjudicate_readings([A, A, B, B], None)
    # 2-2 tie: no cluster reaches a strict majority of the *distinct* clusters;
    # winner size 2 >= 2, so the first cluster wins deterministically.
    assert result.resolved
    assert result.rationale == "majority_agreement"
    assert result.selected_text == A

    split = adjudicate_readings([A, B, C, "雀"], None)  # four distinct readings
    assert split.resolved is False
    assert split.rationale == "engines_disagree"
    assert split.votes is not None and split.votes.split is True


def test_vlm_corroborates_variant_cluster_member() -> None:
    result = adjudicate_readings([A, B, C], VLMReading(0, 0, "m", "1", A_VARIANT, False))
    assert result.resolved
    assert result.actor is DecisionActor.VLM
    assert result.rationale == "vlm_confident"


def test_vote_summary_in_unresolved_results_is_still_present() -> None:
    result = adjudicate_readings([A, B, C], None)
    assert result.votes is not None
    assert result.votes.split is True
    assert result.votes.cluster_sizes == (1, 1, 1)


# ---------------------------------------------------------------------------
# wrapper + votes plumbing
# ---------------------------------------------------------------------------


def test_two_engine_wrapper_carries_votes() -> None:
    from scribeforge.domain.decisions import adjudicate_pair

    result = adjudicate_pair(A, B, None)

    assert result.votes is not None
    assert result.votes.engine_count == 2
    assert result.votes.split is True
    assert result.resolved is False


def test_wrapper_agreement_votes_unanimous() -> None:
    from scribeforge.domain.decisions import adjudicate_pair

    result = adjudicate_pair(A, A_VARIANT, None)

    assert result.resolved
    assert result.votes.unanimous is True
    assert result.votes.cluster_sizes == (2,)


def test_vote_summary_all_missing() -> None:
    summary = summarize_engine_votes([None, None, None])
    assert summary.engine_count == 3
    assert summary.text_count == 0
    assert summary.cluster_sizes == ()
    assert summary.winner_size == 0
    assert not (summary.unanimous or summary.majority or summary.split)


def test_vote_summary_single_reading_is_not_majority() -> None:
    summary = summarize_engine_votes([A, None, None])
    assert summary.text_count == 1
    assert summary.majority is False
    assert summary.split is True  # one reading cannot corroborate itself
    assert summary.unanimous is True  # single cluster holds all produced text


def test_gated_majority_still_reports_votes() -> None:
    from scribeforge.domain.risk import PairRisk, RiskReason

    # A replacement character agreed by a majority is still blocked by the gate.
    risk = PairRisk(0, 0.9, (RiskReason.LOW_CONFIDENCE,))
    result = adjudicate_readings(["�", "�", "�"], None, risk=risk)

    assert result.resolved is False
    assert result.blocked
    assert result.votes is not None and result.votes.unanimous is True
