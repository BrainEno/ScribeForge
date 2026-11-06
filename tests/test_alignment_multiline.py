"""Evidence-level multiline alignment regressions for issue #79.

The JSON fixtures are hand-labelled at the *original OCR line-index* level.  The
metrics deliberately compare groups, never page-wide concatenated text.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from scribeforge.domain.alignment import (
    AlignmentStatus,
    LineAlignment,
    align_pages,
)
from scribeforge.domain.ocr import (
    BlockType,
    BoundingBox,
    OCRBlock,
    OCRLine,
    PageDirection,
    PageOCRResult,
)
from scribeforge.domain.risk import RiskReason, assess_page, review_candidates
from scribeforge.pipeline.adjudication import adjudicate_verification, review_detail
from scribeforge.storage.sqlite import SQLiteStore

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "alignment_groundtruth.json"
_FIXTURES = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _page(engine: str, rows: list[dict[str, object]], direction: PageDirection) -> PageOCRResult:
    lines: list[OCRLine] = []
    blocks: list[OCRBlock] = []
    for index, row in enumerate(rows):
        x, y, width, height = row["box"]  # type: ignore[misc]
        box = BoundingBox(float(x), float(y), float(width), float(height))
        lines.append(OCRLine(str(row["text"]), box))
        # A separate block per line is intentional: compatibility must come from
        # the declared role, not from accidentally sharing one large block.
        blocks.append(OCRBlock(BlockType(str(row["role"])), box, index, index + 1))
    return PageOCRResult(
        0,
        engine,
        "fixture-1",
        tuple(lines),
        blocks=tuple(blocks),
        direction=direction,
        direction_declared=True,
    )


def _pages(fixture: dict[str, object]) -> tuple[PageOCRResult, PageOCRResult]:
    direction = PageDirection(str(fixture["direction"]))
    return (
        _page("mineru", fixture["primary"], direction),  # type: ignore[arg-type]
        _page("paddleocr", fixture["secondary"], direction),  # type: ignore[arg-type]
    )


def _group(pair: LineAlignment) -> tuple[tuple[int, ...], tuple[int, ...]]:
    return pair.primary_line_indexes, pair.secondary_line_indexes


def _labelled_groups(fixture: dict[str, object], key: str = "expected_groups") -> set[
    tuple[tuple[int, ...], tuple[int, ...]]
]:
    return {
        (tuple(item["primary"]), tuple(item["secondary"]))
        for item in fixture.get(key, [])  # type: ignore[union-attr]
    }


@dataclass(frozen=True)
class AlignmentMetrics:
    true_positive: int
    false_positive: int
    false_negative: int
    reading_order_errors: int
    cross_column_errors: int
    unresolved_ambiguities: int
    one_to_many: int
    many_to_one: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return self.true_positive / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return self.true_positive / denominator if denominator else 1.0

    @property
    def f1(self) -> float:
        denominator = self.precision + self.recall
        return 2 * self.precision * self.recall / denominator if denominator else 0.0


def _measure(
    fixture: dict[str, object],
    primary: PageOCRResult,
    secondary: PageOCRResult,
    pairs: tuple[LineAlignment, ...],
) -> AlignmentMetrics:
    expected = _labelled_groups(fixture)
    regular = [pair for pair in pairs if pair.status is AlignmentStatus.MATCHED]
    actual = {_group(pair) for pair in regular}
    expected_order = [
        (tuple(item["primary"]), tuple(item["secondary"]))
        for item in fixture.get("expected_groups", [])  # type: ignore[union-attr]
    ]
    actual_order = [_group(pair) for pair in regular]
    expected_positions = {group: index for index, group in enumerate(expected_order)}
    reading_positions = [
        expected_positions[group] for group in actual_order if group in expected_positions
    ]
    reading_order_errors = sum(
        earlier > later
        for index, earlier in enumerate(reading_positions)
        for later in reading_positions[index + 1 :]
    )

    cross_column_errors = 0
    for pair in regular:
        primary_columns = {
            str(fixture["primary"][index]["column"])  # type: ignore[index]
            for index in pair.primary_line_indexes
        }
        secondary_columns = {
            str(fixture["secondary"][index]["column"])  # type: ignore[index]
            for index in pair.secondary_line_indexes
        }
        if (
            len(primary_columns) != 1
            or len(secondary_columns) != 1
            or primary_columns != secondary_columns
        ):
            cross_column_errors += 1

    return AlignmentMetrics(
        true_positive=len(actual & expected),
        false_positive=len(actual - expected),
        false_negative=len(expected - actual),
        reading_order_errors=reading_order_errors,
        cross_column_errors=cross_column_errors,
        unresolved_ambiguities=sum(
            pair.status is AlignmentStatus.AMBIGUOUS for pair in pairs
        ),
        one_to_many=sum(
            len(pair.primary_line_indexes) == 1 and len(pair.secondary_line_indexes) > 1
            for pair in regular
        ),
        many_to_one=sum(
            len(pair.primary_line_indexes) > 1 and len(pair.secondary_line_indexes) == 1
            for pair in regular
        ),
    )


@pytest.mark.parametrize(
    "fixture",
    [item for item in _FIXTURES if not item.get("expected_ambiguities")],
    ids=lambda item: item["name"],
)
def test_hand_labelled_layout_groups_and_metrics(fixture: dict[str, object]) -> None:
    primary, secondary = _pages(fixture)
    result = align_pages(primary, secondary)
    metrics = _measure(fixture, primary, secondary, result.pairs)

    print(
        f"{fixture['name']}: precision={metrics.precision:.3f} "
        f"recall={metrics.recall:.3f} f1={metrics.f1:.3f} "
        f"order_errors={metrics.reading_order_errors} "
        f"cross_column={metrics.cross_column_errors} "
        f"1:n={metrics.one_to_many} n:1={metrics.many_to_one}"
    )
    assert metrics.precision == metrics.recall == metrics.f1 == 1.0
    assert metrics.reading_order_errors == 0
    assert metrics.cross_column_errors == 0
    assert metrics.unresolved_ambiguities == 0
    assert {
        _group(pair) for pair in result.pairs if pair.status is AlignmentStatus.UNMATCHED
    } == _labelled_groups(fixture, "expected_unmatched")


def test_aggregate_groundtruth_precision_recall_f1_and_structure_counts() -> None:
    measurements: list[AlignmentMetrics] = []
    for fixture in _FIXTURES:
        if fixture.get("expected_ambiguities"):
            continue
        primary, secondary = _pages(fixture)
        measurements.append(
            _measure(fixture, primary, secondary, align_pages(primary, secondary).pairs)
        )
    total = AlignmentMetrics(
        true_positive=sum(item.true_positive for item in measurements),
        false_positive=sum(item.false_positive for item in measurements),
        false_negative=sum(item.false_negative for item in measurements),
        reading_order_errors=sum(item.reading_order_errors for item in measurements),
        cross_column_errors=sum(item.cross_column_errors for item in measurements),
        unresolved_ambiguities=sum(item.unresolved_ambiguities for item in measurements),
        one_to_many=sum(item.one_to_many for item in measurements),
        many_to_one=sum(item.many_to_one for item in measurements),
    )
    print(
        f"aggregate: precision={total.precision:.3f} recall={total.recall:.3f} "
        f"f1={total.f1:.3f} order_errors={total.reading_order_errors} "
        f"cross_column={total.cross_column_errors} "
        f"unresolved_ambiguities={total.unresolved_ambiguities} "
        f"1:n={total.one_to_many} n:1={total.many_to_one}"
    )
    assert (total.precision, total.recall, total.f1) == (1.0, 1.0, 1.0)
    assert total.reading_order_errors == total.cross_column_errors == 0
    assert total.unresolved_ambiguities == 0
    assert total.one_to_many >= 3
    assert total.many_to_one >= 3


def test_close_scores_expose_original_line_alternatives_without_pairing() -> None:
    fixture = next(item for item in _FIXTURES if item.get("expected_ambiguities"))
    primary, secondary = _pages(fixture)

    result = align_pages(primary, secondary)

    assert not [pair for pair in result.pairs if pair.status is AlignmentStatus.MATCHED]
    assert len(result.pairs) == 1
    pair = result.pairs[0]
    expected = fixture["expected_ambiguities"][0]  # type: ignore[index]
    assert pair.status is AlignmentStatus.AMBIGUOUS
    assert pair.is_ambiguous
    assert pair.primary_line_indexes == tuple(expected["primary"])
    assert pair.secondary_line_indexes == tuple(expected["secondary"])
    assert pair.ambiguity is not None
    assert pair.ambiguity.score_margin <= 0.20
    assert {
        (alternative.primary_line_indexes, alternative.secondary_line_indexes)
        for alternative in pair.ambiguity.alternatives
    } == {
        (tuple(item["primary"]), tuple(item["secondary"]))
        for item in expected["alternatives"]
    }


def test_alignment_is_deterministic_including_ambiguity() -> None:
    for fixture in _FIXTURES:
        primary, secondary = _pages(fixture)
        first = align_pages(primary, secondary)
        assert all(align_pages(primary, secondary) == first for _ in range(5)), fixture["name"]


def test_group_size_is_explicitly_bounded() -> None:
    fixture = _FIXTURES[0]
    primary, secondary = _pages(fixture)

    with pytest.raises(ValueError, match="max_group_size"):
        align_pages(primary, secondary, max_group_size=0)
    with pytest.raises(ValueError, match="max_group_size"):
        align_pages(primary, secondary, max_group_size=5)


def test_large_page_remains_bounded_and_preserves_every_source_index() -> None:
    count = 120
    lines = tuple(
        OCRLine(f"第 {index} 行固定文本", BoundingBox(0.10, index / 200, 0.70, 0.003))
        for index in range(count)
    )
    primary = PageOCRResult(0, "primary", "1", lines)
    secondary = PageOCRResult(0, "secondary", "1", lines)

    started = time.perf_counter()
    result = align_pages(primary, secondary, max_cross_axis_distance=0.01)
    elapsed = time.perf_counter() - started

    assert elapsed < 8.0, f"alignment took {elapsed:.3f}s for {count} lines"
    assert len(result.pairs) == count
    assert [pair.primary_line_indexes for pair in result.pairs] == [(i,) for i in range(count)]
    assert [pair.secondary_line_indexes for pair in result.pairs] == [(i,) for i in range(count)]


def test_multiline_alignment_and_ambiguity_round_trip_without_text_loss(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "page.png", 1000, 2000)

    for fixture in (_FIXTURES[0], next(item for item in _FIXTURES if item.get("expected_ambiguities"))):
        primary, secondary = _pages(fixture)
        primary_id = store.record_ocr(project_id, primary)
        secondary_id = store.record_ocr(project_id, secondary)
        alignment = align_pages(primary, secondary)
        risks = assess_page(alignment, primary, secondary)
        candidates = review_candidates(alignment, risks, primary, secondary)
        verification_id = store.record_verification(
            primary_id, secondary_id, alignment, risks, candidates
        )

        restored = store.load_verification(verification_id)
        assert restored.alignment == alignment
        assert store.load_ocr_run(primary_id).lines == primary.lines
        assert store.load_ocr_run(secondary_id).lines == secondary.lines


def test_ambiguous_alignment_cannot_be_machine_accepted(tmp_path: Path) -> None:
    fixture = next(item for item in _FIXTURES if item.get("expected_ambiguities"))
    primary, secondary = _pages(fixture)
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "page.png", 1000, 2000)
    primary_id = store.record_ocr(project_id, primary)
    secondary_id = store.record_ocr(project_id, secondary)
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    candidates = review_candidates(alignment, risks, primary, secondary)
    verification_id = store.record_verification(
        primary_id, secondary_id, alignment, risks, candidates
    )

    outcome = adjudicate_verification(store, project_id, verification_id)

    assert outcome.recorded == 0
    assert outcome.unresolved == (0,)
    assert outcome.blocked == (0,)
    assert RiskReason.ALIGNMENT_AMBIGUITY in risks[0].reasons
    assert candidates[0].pair_index == 0
    assert store.latest_decision(project_id, 0, 0) is None


def test_multiline_decision_cites_the_whole_group_not_only_scalar_anchors(
    tmp_path: Path,
) -> None:
    primary, secondary = _pages(_FIXTURES[0])
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "page.png", 1000, 2000)
    primary_id = store.record_ocr(project_id, primary)
    secondary_id = store.record_ocr(project_id, secondary)
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    verification_id = store.record_verification(
        primary_id, secondary_id, alignment, risks, ()
    )

    outcome = adjudicate_verification(store, project_id, verification_id)
    decision = store.latest_decision(project_id, 0, 0)
    detail = review_detail(store, project_id, 0, 0)

    assert outcome.recorded == 2
    assert decision is not None
    assert decision.selected_text == "春眠不觉晓"
    assert decision.evidence_refs == (
        f"verification_run:{verification_id}",
        f"engine_run:{primary_id}",
        f"engine_run:{secondary_id}",
    )
    assert detail.primary_line_indexes == (0,)
    assert detail.secondary_line_indexes == (0, 1)
    assert detail.primary_box == primary.lines[0].box
    assert detail.secondary_box is not None
    assert (
        detail.secondary_box.x,
        detail.secondary_box.y,
        detail.secondary_box.width,
        detail.secondary_box.height,
    ) == (0.10, 0.18, 0.50, pytest.approx(0.08))
    assert store.load_ocr_run(secondary_id).lines[0].text == "春眠"
    assert store.load_ocr_run(secondary_id).lines[1].text == "不觉晓"


def test_v14_upgrade_preserves_pair_indexes_source_lines_and_human_decision(
    tmp_path: Path,
) -> None:
    """v15 is additive; removing its new tables yields an actual v14 shape."""

    path = tmp_path / "legacy-v14.sqlite3"
    legacy = SQLiteStore(path)
    legacy.initialize()
    project_id = legacy.create_project("Legacy", "old.pdf", "hash")
    legacy.add_page(project_id, 0, "old.png", 1000, 2000)
    primary = PageOCRResult(
        0, "primary", "1", (OCRLine("原文甲", BoundingBox(0.1, 0.1, 0.6, 0.04)),)
    )
    secondary = PageOCRResult(
        0, "secondary", "1", (OCRLine("原文乙", BoundingBox(0.1, 0.1, 0.6, 0.04)),)
    )
    primary_id = legacy.record_ocr(project_id, primary)
    secondary_id = legacy.record_ocr(project_id, secondary)
    old_alignment = align_pages(primary, secondary)
    old_risks = assess_page(old_alignment, primary, secondary)
    verification_id = legacy.record_verification(
        primary_id, secondary_id, old_alignment, old_risks, ()
    )
    decision_id = legacy.append_decision(
        project_id=project_id,
        page_index=0,
        pair_index=0,
        selected_text="人工保留的文字",
        actor="human",
        evidence_refs=(
            f"verification_run:{verification_id}",
            f"engine_run:{primary_id}",
            f"engine_run:{secondary_id}",
        ),
        expected_verification_id=verification_id,
    )

    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            DROP TABLE alignment_alternative_members;
            DROP TABLE alignment_alternatives;
            DROP TABLE alignment_ambiguities;
            DROP TABLE alignment_line_members;
            PRAGMA user_version = 14;
            """
        )

    upgraded = SQLiteStore(path)
    upgraded.initialize()
    restored = upgraded.load_verification(verification_id)
    decision = upgraded.latest_decision(project_id, 0, 0)

    assert SQLiteStore.SCHEMA_VERSION == 15
    assert restored.id == verification_id
    assert upgraded.latest_verification_id(project_id, 0) == verification_id
    assert restored.alignment.pairs[0].primary_line_indexes == (0,)
    assert restored.alignment.pairs[0].secondary_line_indexes == (0,)
    assert restored.alignment.pairs[0].primary_line_index == 0
    assert restored.alignment.pairs[0].secondary_line_index == 0
    assert upgraded.load_ocr_run(primary_id).lines[0].text == "原文甲"
    assert upgraded.load_ocr_run(secondary_id).lines[0].text == "原文乙"
    assert decision is not None
    assert decision.id == decision_id
    assert decision.pair_index == 0
    assert decision.selected_text == "人工保留的文字"
    assert decision.actor == "human"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
        assert connection.execute(
            "SELECT side, member_index, line_index FROM alignment_line_members "
            "ORDER BY side, member_index"
        ).fetchall() == [("primary", 0, 0), ("secondary", 0, 0)]


def test_missing_new_membership_evidence_fails_closed(tmp_path: Path) -> None:
    primary, secondary = _pages(_FIXTURES[0])
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "hash")
    store.add_page(project_id, 0, "page.png", 1000, 2000)
    primary_id = store.record_ocr(project_id, primary)
    secondary_id = store.record_ocr(project_id, secondary)
    alignment = align_pages(primary, secondary)
    risks = assess_page(alignment, primary, secondary)
    verification_id = store.record_verification(
        primary_id, secondary_id, alignment, risks, ()
    )

    with sqlite3.connect(tmp_path / "project.sqlite3") as connection:
        connection.execute("DELETE FROM alignment_line_members WHERE side = 'secondary'")

    with pytest.raises(ValueError, match="membership evidence"):
        store.load_verification(verification_id)
