"""Risk-distribution and report-comparison tests (issue #69).

The processing report already counts failure reasons, but operators also need
the *shape* of the risk distribution: what fraction of aligned pairs sits in
each score bucket, and how it moved between two runs. These tests cover the
pure bucketing function, its integration into the report, and the diff of two
report JSON payloads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribeforge.processing_report import (
    build_processing_report,
    compare_report_dicts,
    processing_report_dict,
    render_comparison_markdown,
    render_processing_report,
    risk_score_distribution,
)
from scribeforge.storage.sqlite import SQLiteStore


@pytest.mark.parametrize(
    "scores,buckets,expected",
    [
        ([], 5, [(0.0, 0.2, 0), (0.2, 0.4, 0), (0.4, 0.6, 0), (0.6, 0.8, 0), (0.8, 1.0, 0)]),
        ([0.0], 5, [(0.0, 0.2, 1), (0.2, 0.4, 0), (0.4, 0.6, 0), (0.6, 0.8, 0), (0.8, 1.0, 0)]),
        # 1.0 lands in the last bucket, not out of range.
        ([1.0], 5, [(0.0, 0.2, 0), (0.2, 0.4, 0), (0.4, 0.6, 0), (0.6, 0.8, 0), (0.8, 1.0, 1)]),
        (
            [0.2, 0.199999, 0.8],
            5,
            [(0.0, 0.2, 1), (0.2, 0.4, 1), (0.4, 0.6, 0), (0.6, 0.8, 0), (0.8, 1.0, 1)],
        ),
        ([0.5, 0.5, 0.5], 2, [(0.0, 0.5, 0), (0.5, 1.0, 3)]),
    ],
)
def test_risk_score_distribution_buckets(scores, buckets, expected) -> None:
    result = risk_score_distribution(scores, bucket_count=buckets)
    assert [(b.low, b.high, b.count) for b in result] == expected
    assert sum(b.count for b in result) == len(scores)


@pytest.mark.parametrize("bad", [0, -1, 21])
def test_risk_score_distribution_rejects_bad_bucket_count(bad: int) -> None:
    with pytest.raises(ValueError, match="bucket_count"):
        risk_score_distribution([0.5], bucket_count=bad)


def test_risk_score_distribution_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValueError, match="risk score"):
        risk_score_distribution([1.5])
    with pytest.raises(ValueError, match="risk score"):
        risk_score_distribution([-0.1])


def _store_with_pages(tmp_path: Path, page_count: int) -> tuple[SQLiteStore, int]:
    from PIL import Image

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("Book", "book.pdf", "source-hash")
    for index in range(page_count):
        image = tmp_path / f"page-{index}.png"
        Image.new("RGB", (100, 150), "white").save(image)
        store.add_page(project_id, index, str(image), 100, 150)
    return store, project_id


def _seed_verification(
    store: SQLiteStore, project_id: int, page_index: int, scores: list[float]
) -> None:
    """Record a verification with the given risk scores for one page."""
    from scribeforge.domain.alignment import LineAlignment, PageAlignment
    from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
    from scribeforge.domain.risk import PairRisk, RiskReason

    lines = tuple(
        OCRLine(f"t{i}", BoundingBox(0.1, 0.05 + 0.08 * i, 0.8, 0.05)) for i in range(len(scores))
    )
    primary = store.record_ocr(project_id, PageOCRResult(page_index, "primary", "1", lines))
    secondary = store.record_ocr(project_id, PageOCRResult(page_index, "secondary", "1", lines))
    alignment = PageAlignment(
        page_index,
        "primary",
        "secondary",
        tuple(LineAlignment(i, i, 1.0, 0.0, ()) for i in range(len(scores))),
    )
    risks = tuple(
        PairRisk(i, score, (RiskReason.LOW_CONFIDENCE,)) for i, score in enumerate(scores)
    )
    store.record_verification(primary, secondary, alignment, risks, ())


def test_build_report_includes_risk_distribution(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 2)
    _seed_verification(store, project_id, 0, [0.1, 0.9, 0.95])
    _seed_verification(store, project_id, 1, [0.3])
    report = build_processing_report(store, project_id, high_risk=0.6)

    assert len(report.risk_distribution) == 5
    total = sum(b.count for b in report.risk_distribution)
    assert total == 4
    high = sum(b.count for b in report.risk_distribution if b.low >= 0.6)
    assert high == 2
    assert report.high_risk_pairs_total == 2

    text = render_processing_report(report)
    assert "## Risk distribution" in text
    assert "0.8" in text  # bucket boundary rendered

    payload = processing_report_dict(report)
    assert "risk_distribution" in payload
    buckets = payload["risk_distribution"]
    assert isinstance(buckets, list) and len(buckets) == 5
    assert sum(b["count"] for b in buckets) == 4


def test_report_without_verifications_has_empty_distribution(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 1)
    report = build_processing_report(store, project_id)
    assert sum(b.count for b in report.risk_distribution) == 0
    assert report.high_risk_pairs_total == 0
    text = render_processing_report(report)
    assert "## Risk distribution" in text


def _payload(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "project_id": 1,
        "pages_total": 10,
        "ocr_complete_pages": 8,
        "failed_pages": 1,
        "running_pages": 0,
        "review": {"review_required_ratio": 0.25},
        "timing": {"observed_seconds": 100.0},
        "risk_distribution": [
            {"low": 0.0, "high": 0.2, "count": 5},
            {"low": 0.2, "high": 0.4, "count": 3},
            {"low": 0.4, "high": 0.6, "count": 2},
            {"low": 0.6, "high": 0.8, "count": 1},
            {"low": 0.8, "high": 1.0, "count": 1},
        ],
    }
    base.update(overrides)
    return base


def test_compare_report_dicts_detects_changes() -> None:
    old = _payload()
    new = _payload(failed_pages=3, ocr_complete_pages=6)
    new["review"]["review_required_ratio"] = 0.5
    new["timing"]["observed_seconds"] = 250.0
    diff = compare_report_dicts(old, new)
    assert diff.failed_pages_delta == 2
    assert diff.ocr_complete_delta == -2
    assert diff.review_ratio_delta == pytest.approx(0.25)
    assert diff.observed_seconds_delta == pytest.approx(150.0)
    assert diff.risk_bucket_deltas == {}  # unchanged distribution records no deltas


def test_compare_report_dicts_risk_bucket_deltas() -> None:
    old = _payload()
    new = _payload()
    new["risk_distribution"] = [
        {"low": 0.0, "high": 0.2, "count": 2},
        {"low": 0.2, "high": 0.4, "count": 3},
        {"low": 0.4, "high": 0.6, "count": 2},
        {"low": 0.6, "high": 0.8, "count": 4},
        {"low": 0.8, "high": 1.0, "count": 1},
    ]
    diff = compare_report_dicts(old, new)
    assert diff.risk_bucket_deltas[0] == -3
    assert diff.risk_bucket_deltas[3] == 3


def test_compare_report_dicts_rejects_schema_mismatch() -> None:
    with pytest.raises(ValueError, match="schema"):
        compare_report_dicts(_payload(schema_version=9), _payload())


def test_compare_report_dicts_tolerates_missing_distribution() -> None:
    old = _payload()
    del old["risk_distribution"]
    diff = compare_report_dicts(old, _payload())
    assert diff.risk_bucket_deltas == {}


def test_render_comparison_markdown() -> None:
    old = _payload()
    new = _payload(failed_pages=4)
    new["risk_distribution"] = [dict(b) for b in old["risk_distribution"]]
    new["risk_distribution"][4]["count"] = 5
    diff = compare_report_dicts(old, new)
    text = render_comparison_markdown(diff)
    assert "# Report comparison" in text
    assert "failed_pages" in text
    assert "+3" in text
    assert "Risk distribution delta" in text
    assert "+4" in text


def test_cli_report_compare_roundtrip(tmp_path: Path, capsys) -> None:
    import io

    from scribeforge.cli import run

    store, project_id = _store_with_pages(tmp_path, 2)
    _seed_verification(store, project_id, 0, [0.1, 0.9])
    report = build_processing_report(store, project_id)
    old_path = tmp_path / "old.json"
    old_path.write_text(json.dumps(processing_report_dict(report)), encoding="utf-8")

    out = io.StringIO()
    code = run(
        ["report", "--store", str(tmp_path), "--json"],
        stdout=out,
    )
    assert code == 0
    json.loads(out.getvalue())  # current report is valid schema-v1 JSON

    out2 = io.StringIO()
    code = run(
        ["report", "--store", str(tmp_path), "--compare", str(old_path)],
        stdout=out2,
    )
    assert code == 0
    text = out2.getvalue()
    assert "# Report comparison" in text


def test_compare_tolerates_missing_optional_fields() -> None:
    old = {"schema_version": 1}
    new = {"schema_version": 1, "pages_total": 5, "failed_pages": 2}
    diff = compare_report_dicts(old, new)
    assert diff.pages_total_delta == 5
    assert diff.failed_pages_delta == 2
    assert diff.review_ratio_delta == 0.0
    assert diff.observed_seconds_delta is None
    assert diff.risk_bucket_deltas == {}


def test_compare_rejects_non_dict() -> None:
    with pytest.raises(TypeError, match="JSON object"):
        compare_report_dicts([1, 2], _payload())  # type: ignore[arg-type]


def test_compare_bucket_length_mismatch_tolerated() -> None:
    old = _payload()
    new = _payload()
    new["risk_distribution"] = [{"low": 0.0, "high": 1.0, "count": 12}]
    diff = compare_report_dicts(old, new)
    assert diff.risk_bucket_deltas[0] == 12 - 5
    assert diff.risk_bucket_deltas[1] == -3
    assert diff.risk_bucket_deltas[4] == -1


def test_distribution_bucket_boundaries_are_inclusive_low() -> None:
    # 0.2 belongs to the second bucket [0.2, 0.4), 0.6 to [0.6, 0.8), etc.
    result = risk_score_distribution([0.2, 0.4, 0.6, 0.8, 1.0], bucket_count=5)
    assert [b.count for b in result] == [0, 1, 1, 1, 2]


def test_build_report_high_risk_threshold_respected(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.5, 0.65, 0.9])
    report = build_processing_report(store, project_id, high_risk=0.6)
    assert report.high_risk_pairs_total == 2
    report_low = build_processing_report(store, project_id, high_risk=0.4)
    assert report_low.high_risk_pairs_total == 3


def test_report_json_contains_distribution_and_is_schema_v1(tmp_path: Path) -> None:
    from scribeforge.processing_report import processing_report_json

    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.1, 0.7])
    payload = json.loads(processing_report_json(build_processing_report(store, project_id)))
    assert payload["schema_version"] == 1
    assert payload["high_risk_pairs_total"] == 1
    assert [b["count"] for b in payload["risk_distribution"]] == [1, 0, 0, 1, 0]


def test_cli_report_compare_missing_file_is_fatal(tmp_path: Path, capsys) -> None:
    import io

    from scribeforge.cli import run

    store, project_id = _store_with_pages(tmp_path, 1)
    build_processing_report(store, project_id)
    code = run(
        ["report", "--store", str(tmp_path), "--compare", str(tmp_path / "nope.json")],
        stdout=io.StringIO(),
    )
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_cli_report_compare_bad_json_is_fatal(tmp_path: Path, capsys) -> None:
    import io

    from scribeforge.cli import run

    store, project_id = _store_with_pages(tmp_path, 1)
    build_processing_report(store, project_id)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code = run(
        ["report", "--store", str(tmp_path), "--compare", str(bad)],
        stdout=io.StringIO(),
    )
    assert code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_report_compare_schema_mismatch_is_fatal(tmp_path: Path, capsys) -> None:
    import io

    from scribeforge.cli import run

    store, project_id = _store_with_pages(tmp_path, 1)
    build_processing_report(store, project_id)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    code = run(
        ["report", "--store", str(tmp_path), "--compare", str(wrong)],
        stdout=io.StringIO(),
    )
    assert code == 2
    assert "schema" in capsys.readouterr().err


def test_cli_report_parser_exposes_compare() -> None:
    from scribeforge.cli import build_parser

    args = build_parser().parse_args(["report", "--store", "s", "--compare", "old.json"])
    assert args.compare == Path("old.json")
    default = build_parser().parse_args(["report", "--store", "s"])
    assert default.compare is None


def test_distribution_bar_scales_to_max_bucket(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.1] * 8 + [0.9] * 2)
    text = render_processing_report(build_processing_report(store, project_id))
    # the largest bucket gets a 20-char bar, the smaller one is scaled down
    assert "####################" in text
    assert "##" in text


def test_distribution_handles_single_score_at_zero() -> None:
    result = risk_score_distribution([0.0], bucket_count=10)
    assert result[0].count == 1
    assert sum(b.count for b in result[1:]) == 0
    assert result[0].low == 0.0 and result[0].high == pytest.approx(0.1)


def test_distribution_twenty_buckets_max() -> None:
    result = risk_score_distribution([0.999], bucket_count=20)
    assert len(result) == 20
    assert result[-1].count == 1


def test_compare_delta_text_formatting() -> None:
    from scribeforge.processing_report import _delta_text

    assert _delta_text(3) == "+3"
    assert _delta_text(-2.5) == "-2.5"
    assert _delta_text(0) == "0"


def test_compare_observed_seconds_missing_on_either_side() -> None:
    old = _payload()
    new = _payload()
    new["timing"]["observed_seconds"] = None
    diff = compare_report_dicts(old, new)
    assert diff.observed_seconds_delta is None


def test_compare_negative_deltas() -> None:
    old = _payload(failed_pages=5)
    new = _payload(failed_pages=1)
    diff = compare_report_dicts(old, new)
    assert diff.failed_pages_delta == -4
    text = render_comparison_markdown(diff)
    assert "-4" in text


def test_render_report_distribution_section_with_pairs(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.05, 0.95, 0.65])
    report = build_processing_report(store, project_id)
    text = render_processing_report(report)
    assert "[0.0, 0.2]" in text
    assert "[0.8, 1.0]" in text
    assert "high-risk pairs (score >= 0.6): 2" in text


def test_render_report_distribution_empty(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 1)
    text = render_processing_report(build_processing_report(store, project_id))
    assert "No scored verification pairs yet." in text


def test_report_dict_distribution_matches_render(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.3, 0.7])
    report = build_processing_report(store, project_id)
    payload = processing_report_dict(report)
    counts = [b["count"] for b in payload["risk_distribution"]]
    assert counts == [0, 1, 0, 1, 0]
    assert payload["high_risk_pairs_total"] == 1


def test_cli_report_compare_success_json_exported(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run
    from scribeforge.processing_report import processing_report_json

    store, project_id = _store_with_pages(tmp_path, 2)
    _seed_verification(store, project_id, 0, [0.2])
    report = build_processing_report(store, project_id)
    old_path = tmp_path / "old.json"
    old_path.write_text(processing_report_json(report), encoding="utf-8")

    # add a failing verification so the new report differs
    _seed_verification(store, project_id, 1, [0.9, 0.95])
    out = io.StringIO()
    code = run(["report", "--store", str(tmp_path), "--compare", str(old_path)], stdout=out)
    assert code == 0
    text = out.getvalue()
    assert "# Report comparison" in text
    assert "bucket 4" in text  # two new pairs in the top bucket
    assert "+2" in text


def test_distribution_aggregates_across_pages(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 3)
    _seed_verification(store, project_id, 0, [0.1, 0.9])
    _seed_verification(store, project_id, 1, [0.5])
    _seed_verification(store, project_id, 2, [0.7, 0.75, 0.3])
    report = build_processing_report(store, project_id)
    counts = [b.count for b in report.risk_distribution]
    assert counts == [1, 1, 1, 2, 1]
    assert report.high_risk_pairs_total == 3  # 0.9, 0.7, 0.75 with threshold 0.6


def test_distribution_uses_latest_verification_only(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.1, 0.1])
    _seed_verification(store, project_id, 0, [0.9])
    report = build_processing_report(store, project_id)
    counts = [b.count for b in report.risk_distribution]
    assert counts == [0, 0, 0, 0, 1]


def test_compare_report_dicts_is_direction_sensitive() -> None:
    a = _payload(failed_pages=1)
    b = _payload(failed_pages=4)
    assert compare_report_dicts(a, b).failed_pages_delta == 3
    assert compare_report_dicts(b, a).failed_pages_delta == -3


def test_render_comparison_without_risk_deltas() -> None:
    from scribeforge.processing_report import render_comparison_markdown

    diff = compare_report_dicts(_payload(), _payload(pages_total=12))
    text = render_comparison_markdown(diff)
    assert "pages_total | +2" in text
    assert "Risk distribution delta" not in text


def test_cli_report_with_events_and_compare(tmp_path: Path) -> None:
    import io

    from scribeforge.cli import run
    from scribeforge.pipeline.events import event_line
    from scribeforge.processing_report import processing_report_json

    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.4])
    report = build_processing_report(store, project_id)
    old_path = tmp_path / "old.json"
    old_path.write_text(processing_report_json(report), encoding="utf-8")
    events = tmp_path / "e.jsonl"
    events.write_text(
        event_line("book_finished", {"elapsed_seconds": 3.0}, now=1.0) + "\n", encoding="utf-8"
    )

    out = io.StringIO()
    code = run(
        ["report", "--store", str(tmp_path), "--events", str(events), "--compare", str(old_path)],
        stdout=out,
    )
    assert code == 0
    assert "# Report comparison" in out.getvalue()


def test_distribution_scores_at_exact_edges(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.2, 0.4, 0.6, 0.8])
    report = build_processing_report(store, project_id)
    counts = [b.count for b in report.risk_distribution]
    assert counts == [0, 1, 1, 1, 1]


def test_high_risk_pairs_total_zero_without_verifications(tmp_path: Path) -> None:
    store, project_id = _store_with_pages(tmp_path, 2)
    report = build_processing_report(store, project_id)
    assert report.high_risk_pairs_total == 0
    payload = processing_report_dict(report)
    assert payload["high_risk_pairs_total"] == 0
    assert all(b["count"] == 0 for b in payload["risk_distribution"])


def test_distribution_bucket_count_customizable_in_report(tmp_path: Path) -> None:
    # build_processing_report always uses the default 5 buckets; the pure
    # function is what accepts a custom count (covered above). Here we assert
    # the report-level default is exactly 5 buckets spanning [0, 1].
    store, project_id = _store_with_pages(tmp_path, 1)
    _seed_verification(store, project_id, 0, [0.5])
    report = build_processing_report(store, project_id)
    assert len(report.risk_distribution) == 5
    assert report.risk_distribution[0].low == 0.0
    assert report.risk_distribution[-1].high == 1.0


def test_compare_report_dicts_bool_fields_ignored(tmp_path: Path) -> None:
    old = _payload()
    old["failed_pages"] = True  # bool is an int subclass; must not count
    diff = compare_report_dicts(old, _payload(failed_pages=2))
    assert diff.failed_pages_delta == 2


def test_render_comparison_observed_seconds_delta_rendered(tmp_path: Path) -> None:
    from scribeforge.processing_report import render_comparison_markdown

    old = _payload()
    new = _payload()
    new["timing"]["observed_seconds"] = 175.5
    diff = compare_report_dicts(old, new)
    assert diff.observed_seconds_delta == pytest.approx(75.5)
    text = render_comparison_markdown(diff)
    assert "+75.5" in text


def test_report_title_with_pipe_is_escaped_in_markdown(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "p.sqlite3")
    store.initialize()
    project_id = store.create_project("Book | private", "b.pdf", "h")
    from PIL import Image

    img = tmp_path / "p.png"
    Image.new("RGB", (10, 10), "white").save(img)
    store.add_page(project_id, 0, str(img), 10, 10)
    text = render_processing_report(build_processing_report(store, project_id))
    assert "Book \\| private" in text
