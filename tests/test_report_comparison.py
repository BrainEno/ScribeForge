from __future__ import annotations

import json
from pathlib import Path

import pytest

from scribeforge.cli import run
from scribeforge.report_comparison import (
    PAGE_COUNTS,
    PROJECT_COUNTS,
    REVIEW_COUNTS,
    STAGE_COUNTS,
    TIMING_SCOPE,
    CompareOptions,
    compare_snapshots,
    load_snapshot,
)


def _report(values=(0, 3, None), *, title="測試"):
    pages = []
    for index, seconds in enumerate(values):
        pages.append(
            {
                "page_index": index,
                "status": "ocr_complete",
                **dict.fromkeys(PAGE_COUNTS, 0),
                "observed_seconds": seconds,
                "risk_reasons": {},
                "stage_timings": [
                    {
                        "stage": "primary_ocr",
                        "state": "succeeded",
                        "attempts": 1,
                        "observed_seconds": seconds,
                    }
                ],
            }
        )
    measured = [value for value in values if value is not None]
    seconds = sum(measured) if measured else None
    return {
        "schema_version": 1,
        "project_id": 1,
        "title": title,
        **dict.fromkeys(PROJECT_COUNTS, 0),
        "pages_total": len(values),
        "review": dict.fromkeys(REVIEW_COUNTS, 0),
        "timing": {
            "scope": TIMING_SCOPE,
            "observed_seconds": seconds,
            "timed_jobs": len(measured),
            "unavailable_jobs": len(values) - len(measured),
        },
        "stages": [
            {
                **dict.fromkeys(STAGE_COUNTS, 0),
                "stage": "primary_ocr",
                "jobs": len(values),
                "succeeded": len(values),
                "timed_jobs": len(measured),
                "observed_seconds": seconds,
            }
        ],
        "risk_reasons": {},
        "pages": pages,
    }


def _save(path: Path, body):
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_comparison_pins_sort_order_and_filters(tmp_path: Path, capsys) -> None:
    baseline = _save(tmp_path / "基準.json", _report())
    candidate = _report((9, 1, None))
    candidate["pages"][1].update(status="failed", open_pairs=3, risk_reasons={"confidence": 2})
    candidate["risk_reasons"] = {"confidence": 2}
    candidate["review"]["open_pairs"] = 3
    newer = _save(tmp_path / "新版.json", candidate)
    args = ["compare-processing-reports", f"--baseline={baseline}", f"--candidate={newer}"]
    before = [path.read_bytes() for path in (baseline, newer)]
    assert run([*args, "--order=slowdown", "--limit=1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["same_timing_coverage"] and result["seconds"]["delta"] == 7
    assert result["pages"][0]["seconds"] == {"old": 0.0, "new": 9.0, "delta": 9.0}
    assert result["reasons"]["confidence"] == {"old": 0, "new": 2, "delta": 2}
    assert result["page_counts"] == {
        "all": 3,
        "changed": 2,
        "timing": 2,
        "review": 1,
        "status": 1,
        "coverage": 0,
    }
    page_args = [
        "--order=slowdown",
        "--limit=1",
        f"--offset={result['next_offset']}",
        f"--baseline-sha256={result['baseline']['sha256']}",
        f"--candidate-sha256={result['candidate']['sha256']}",
    ]
    assert run([*args, *page_args]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["next_offset"] is None
    assert second["pages"][0]["page_index"] == 1
    assert second["pages"][0]["counts"]["open_pairs"]["delta"] == 3
    assert [path.read_bytes() for path in (baseline, newer)] == before
    newer.write_bytes(before[1] + b"\n")
    assert run([*args, *page_args]) == 2
    assert "report bytes changed" in capsys.readouterr().err
    assert run([*args, "--offset=1"]) == 2
    assert "requires both report digests" in capsys.readouterr().err


def test_coverage_change_suppresses_timing_difference_even_at_equal_count(tmp_path: Path) -> None:
    left = load_snapshot(_save(tmp_path / "old.json", _report((0, 3, None))))
    right = load_snapshot(_save(tmp_path / "new.json", _report((None, 3, 10))))
    result = compare_snapshots(left, right, CompareOptions(filter="coverage"))
    assert result["matched"] == 2
    assert result["seconds"] == {"old": 3.0, "new": 13.0, "delta": None}
    assert result["stages"][0]["seconds"]["delta"] is None
    assert result["pages"][0]["seconds"] == {"old": 0.0, "new": None, "delta": None}


def test_nonaligned_reports_require_opt_in_and_preserve_absent_values(tmp_path: Path) -> None:
    left = load_snapshot(_save(tmp_path / "old.json", _report((7,))))
    payload = _report((8, 1), title="Different")
    payload["timing"]["scope"] = "different_clock"
    right = load_snapshot(_save(tmp_path / "new.json", payload))
    overview = compare_snapshots(left, right, CompareOptions())
    assert overview["same_labels"] is False
    assert overview["page_comparison"] is False
    assert overview["pages"] == []
    assert overview["summary"]["pages_total"]["delta"] == 1
    full = compare_snapshots(left, right, CompareOptions(allow_unmatched=True, filter="all"))
    assert full["pages"][0]["seconds"]["delta"] is None
    assert full["pages"][0]["stages"][0]["seconds"]["delta"] is None
    assert full["pages"][1]["old_status"] is None
    assert full["pages"][1]["counts"]["open_pairs"] == {"old": None, "new": 0, "delta": None}


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    [
        ("schema_version", True, "safe integer"),
        ("schema_version", 2, "unsupported"),
        ("pages_total", 60_000, "exceeds limit"),
        ("pages", [], "every declared page"),
        ("title", "\ud800", "invalid Unicode"),
        ("review", [], "expected an object"),
        ("risk_reasons", {"bad": -1}, "reason count"),
    ],
)
def test_malformed_reports_rejected(tmp_path: Path, field, invalid, message) -> None:
    payload = _report()
    payload[field] = invalid
    with pytest.raises(ValueError, match=message):
        load_snapshot(_save(tmp_path / "report.json", payload))


def test_duplicate_json_and_invalid_stage_or_page_records(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_bytes(b'{"title":"before","title":"after"}')
    with pytest.raises(ValueError, match="duplicate JSON"):
        load_snapshot(path)
    path.write_bytes(b'{"title":NaN}')
    with pytest.raises(ValueError, match="invalid JSON constant"):
        load_snapshot(path)
    payload = _report()
    payload["pages"][1]["page_index"] = 0
    with pytest.raises(ValueError, match="duplicate page"):
        load_snapshot(_save(path, payload))
    payload = _report()
    timings = payload["pages"][0]["stage_timings"]
    timings.append(timings[0])
    with pytest.raises(ValueError, match="duplicate stage"):
        load_snapshot(_save(path, payload))
    payload = _report()
    payload["pages"][0]["observed_seconds"] = 4
    with pytest.raises(ValueError, match="timing total"):
        load_snapshot(_save(path, payload))


def test_refuses_nonfiles_and_over_limit_input(tmp_path: Path, monkeypatch) -> None:
    from scribeforge import report_comparison

    with pytest.raises(ValueError, match="regular file"):
        load_snapshot(tmp_path)
    monkeypatch.setattr(report_comparison, "MAX_BYTES", 2)
    path = _save(tmp_path / "report.json", _report())
    with pytest.raises(ValueError, match="exceeds"):
        load_snapshot(path)
