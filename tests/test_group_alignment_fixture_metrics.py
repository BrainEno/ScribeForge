"""Metrics over explicitly labeled synthetic examples, not real OCR accuracy."""
from __future__ import annotations

import json
from pathlib import Path

from scribeforge.domain.group_alignment import propose_group_alignment
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageDirection, PageOCRResult


def test_labeled_group_proposals_report_all_requested_metrics() -> None:
    fixtures = json.loads((Path(__file__).parent / "fixtures" / "group_alignment_truth.json").read_text(encoding="utf-8"))
    tp = predicted = gold = order_errors = cross_region = ambiguous = splits = merges = 0
    def make(engine: str, lines: list[dict]) -> PageOCRResult:
        return PageOCRResult(0, engine, "fixture", tuple(
            OCRLine(line["text"], BoundingBox(*line["box"])) for line in lines
        ))
    for fixture in fixtures:
        result = propose_group_alignment(make("a", fixture["primary"]), make("b", fixture["secondary"]),
                                         direction=PageDirection(fixture["direction"]))
        expected = [(tuple(pair[0]), tuple(pair[1])) for pair in fixture["matches"]]
        actual = [(group.primary_indexes, group.secondary_indexes)
                  for group in result.groups if group.status == "proposed"]
        actual_set, expected_set = set(actual), set(expected)
        tp += len(actual_set & expected_set)
        predicted += len(actual_set)
        gold += len(expected_set)
        indexes = [expected.index(pair) for pair in actual if pair in expected_set]
        order_errors += sum(a > b for i, a in enumerate(indexes) for b in indexes[i + 1:])
        cross_region += sum(
            fixture["primary"][p]["region"] != fixture["secondary"][s]["region"]
            for left, right in actual for p in left for s in right
        )
        ambiguous += sum(group.status == "ambiguous" for group in result.groups)
        splits += sum(len(left) == 1 and len(right) == 2 for left, right in actual)
        merges += sum(len(left) == 2 and len(right) == 1 for left, right in actual)
        assert actual_set == expected_set, fixture["name"]
        assert sum(group.status == "ambiguous" for group in result.groups) == fixture["ambiguous"]
    precision, recall = tp / predicted, tp / gold
    f1 = 2 * precision * recall / (precision + recall)
    assert (tp, predicted, gold, precision, recall, f1) == (8, 8, 8, 1.0, 1.0, 1.0)
    assert (order_errors, cross_region, ambiguous, splits, merges) == (0, 0, 3, 3, 1)
