"""Read-only comparison of saved processing-report snapshots, not OCR evidence."""

from __future__ import annotations

import hashlib
import json
import math
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_BYTES = 16 * 1024 * 1024
MAX_PAGES = 50_000
TIMING_SCOPE = "latest_attempt_timestamp_spans"
FILTERS = ("changed", "all", "timing", "review", "status", "coverage")
ORDERS = ("page", "slowdown", "speedup", "review")
PROJECT_COUNTS = ("pages_total", "ocr_complete_pages", "failed_pages", "running_pages")
REVIEW_COUNTS = (
    "pairs",
    "accepted_automatic",
    "accepted_vlm",
    "accepted_human",
    "open_pairs",
    "open_high_risk",
    "open_blocked",
)
PAGE_COUNTS = ("aligned_pairs", "high_risk_pairs", "open_pairs")
STAGE_COUNTS = ("jobs", "pending", "running", "succeeded", "failed", "attempts", "timed_jobs")


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field}: expected an object")  # noqa: TRY004 -- invalid report
    return value


def _sequence(value: object, field: str, limit: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"{field}: expected at most {limit} entries")
    return value


def _text(value: object, field: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{field}: expected nonempty text (max {limit} characters)")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{field}: invalid Unicode")
    return value


def _count(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 2**53 - 1:
        raise ValueError(f"{field}: expected a nonnegative safe integer")
    return value


def _seconds(value: object, field: str) -> float | None:
    if value is None:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= value <= 1e15
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field}: expected finite nonnegative seconds or null")
    return float(value)


def _counts(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, int]:
    return {key: _count(row.get(key), key) for key in fields}


def _reason_map(value: object) -> dict[str, int]:
    rows = _object(value, "risk_reasons")
    if len(rows) > 128:
        raise ValueError("risk_reasons: too many entries")
    return {_text(key, "reason"): _count(count, "reason count") for key, count in rows.items()}


def _unique_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


@dataclass(frozen=True, slots=True)
class PageStage:
    state: str
    attempts: int
    seconds: float | None


@dataclass(frozen=True, slots=True)
class Page:
    index: int
    status: str
    counts: dict[str, int]
    reasons: dict[str, int]
    seconds: float | None
    stages: dict[str, PageStage]

    @property
    def measured_stages(self) -> frozenset[str]:
        return frozenset(key for key, value in self.stages.items() if value.seconds is not None)


@dataclass(frozen=True, slots=True)
class Stage:
    counts: dict[str, int]
    seconds: float | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    digest: str
    title: str
    project_id: int
    counts: dict[str, int]
    review: dict[str, int]
    timing_scope: str
    seconds: float | None
    timed_jobs: int
    unavailable_jobs: int
    reasons: dict[str, int]
    stages: dict[str, Stage]
    pages: dict[int, Page]

    def header(self) -> dict[str, object]:
        return {
            "sha256": self.digest,
            "title": self.title,
            "project_id": self.project_id,
            "timing_scope": self.timing_scope,
            "timed_jobs": self.timed_jobs,
            "unavailable_jobs": self.unavailable_jobs,
        }


def _page(value: object, maximum: int) -> Page:
    row = _object(value, "page")
    index = _count(row.get("page_index"), "page_index")
    if index >= maximum:
        raise ValueError("page_index outside declared page count")
    stages: dict[str, PageStage] = {}
    for raw in _sequence(row.get("stage_timings"), "stage_timings", 32):
        item = _object(raw, "stage timing")
        key = _text(item.get("stage"), "stage")
        if key in stages:
            raise ValueError("duplicate stage in page")
        stages[key] = PageStage(
            _text(item.get("state"), "state"),
            _count(item.get("attempts"), "attempts"),
            _seconds(item["observed_seconds"], "stage seconds"),
        )
    seconds = _seconds(row["observed_seconds"], "page seconds")
    measured = [item.seconds for item in stages.values() if item.seconds is not None]
    if (seconds is None) != (not measured) or (
        seconds is not None and not math.isclose(seconds, sum(measured), rel_tol=1e-8)
    ):
        raise ValueError(f"page {index + 1}: timing total does not match measured stages")
    return Page(
        index,
        _text(row.get("status"), "status"),
        _counts(row, PAGE_COUNTS),
        _reason_map(row.get("risk_reasons")),
        seconds,
        stages,
    )


def load_snapshot(path: Path) -> Snapshot:
    """Accept the existing complete v1 JSON snapshot; reject ambiguous input."""
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("report must be a regular file")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("report exceeds 16 MiB")
    try:
        body = _object(
            json.loads(
                data.decode("utf-8-sig"),
                object_pairs_hook=_unique_json,
                parse_constant=_reject_constant,
            ),
            "report",
        )
        if _count(body.get("schema_version"), "schema_version") != 1:
            raise ValueError("unsupported processing report schema")
        counts = _counts(body, PROJECT_COUNTS)
        pages: dict[int, Page] = {}
        maximum = counts["pages_total"]
        if maximum > MAX_PAGES:
            raise ValueError("page count exceeds limit")
        for raw in _sequence(body.get("pages"), "pages", MAX_PAGES):
            page = _page(raw, maximum)
            if page.index in pages:
                raise ValueError("duplicate page index")
            pages[page.index] = page
        if len(pages) != maximum:
            raise ValueError("report must include every declared page")
        stages: dict[str, Stage] = {}
        for raw in _sequence(body.get("stages"), "stages", 32):
            row = _object(raw, "stage")
            key = _text(row.get("stage"), "stage")
            if key in stages:
                raise ValueError("duplicate aggregate stage")
            stages[key] = Stage(
                _counts(row, STAGE_COUNTS), _seconds(row["observed_seconds"], "stage seconds")
            )
        timing = _object(body.get("timing"), "timing")
        return Snapshot(
            hashlib.sha256(data).hexdigest(),
            _text(body.get("title"), "title", 4096),
            _count(body.get("project_id"), "project_id"),
            counts,
            _counts(_object(body.get("review"), "review"), REVIEW_COUNTS),
            _text(timing.get("scope"), "timing.scope"),
            _seconds(timing["observed_seconds"], "timing seconds"),
            _count(timing.get("timed_jobs"), "timed_jobs"),
            _count(timing.get("unavailable_jobs"), "unavailable_jobs"),
            _reason_map(body.get("risk_reasons")),
            stages,
            pages,
        )
    except KeyError as exc:
        raise ValueError(f"missing report member: {exc}") from exc
    except RecursionError as exc:
        raise ValueError("report nesting exceeds limit") from exc


@dataclass(frozen=True, slots=True)
class CompareOptions:
    filter: str = "changed"
    order: str = "page"
    offset: int = 0
    limit: int = 30
    old_digest: str | None = None
    new_digest: str | None = None
    allow_unmatched: bool = False

    def check(self, old: Snapshot, new: Snapshot) -> None:
        if self.filter not in FILTERS or self.order not in ORDERS:
            raise ValueError("unsupported page filter or order")
        if not 0 <= self.offset <= MAX_PAGES or not 1 <= self.limit <= 100:
            raise ValueError("expected offset 0–50000 and limit 1–100")
        if self.offset and not (self.old_digest and self.new_digest):
            raise ValueError("pagination requires both report digests")
        if (
            self.old_digest is not None
            and self.old_digest != old.digest
            or self.new_digest is not None
            and self.new_digest != new.digest
        ):
            raise ValueError("report bytes changed; restart comparison from the first page")


def _delta(
    old: float | None, new: float | None, *, comparable: bool = True
) -> dict[str, int | float | None]:
    return {
        "old": old,
        "new": new,
        "delta": new - old if comparable and old is not None and new is not None else None,
    }


def _deltas(
    old: dict[str, int], new: dict[str, int], *, absent_zero: bool = False
) -> dict[str, dict[str, int | float | None]]:
    missing = 0 if absent_zero else None
    return {
        key: _delta(old.get(key, missing), new.get(key, missing))
        for key in sorted(old.keys() | new.keys())
    }


@dataclass(frozen=True, slots=True)
class PageChange:
    index: int
    old: Page | None
    new: Page | None
    same_scope: bool

    @property
    def coverage_changed(self) -> bool:
        return (
            self.old is None
            or self.new is None
            or self.old.measured_stages != self.new.measured_stages
        )

    @property
    def seconds_delta(self) -> float | None:
        if not self.same_scope or self.coverage_changed or not self.old or not self.new:
            return None
        if self.old.seconds is None or self.new.seconds is None:
            return None
        return self.new.seconds - self.old.seconds

    @property
    def review_delta(self) -> int | None:
        if self.old is None or self.new is None:
            return None
        return self.new.counts["open_pairs"] - self.old.counts["open_pairs"]

    def matches(self, category: str) -> bool:
        if category == "all":
            return True
        if category == "coverage":
            return self.coverage_changed
        if category == "changed":
            return self.old != self.new
        if self.old is None or self.new is None:
            return False
        if category == "timing":
            return (
                self.seconds_delta not in (None, 0)
                or any(
                    self.old.stages[key].seconds != self.new.stages[key].seconds
                    for key in self.old.measured_stages & self.new.measured_stages
                )
                and self.same_scope
            )
        if category == "review":
            return self.old.counts != self.new.counts or self.old.reasons != self.new.reasons
        return self.old.status != self.new.status or {
            key: (stage.state, stage.attempts) for key, stage in self.old.stages.items()
        } != {key: (stage.state, stage.attempts) for key, stage in self.new.stages.items()}

    def key(self, order: str) -> tuple[bool, float, int]:
        seconds = self.seconds_delta
        value = (
            0.0
            if order == "page"
            else ((-seconds if order == "slowdown" else seconds) if seconds is not None else None)
        )
        if order == "review":
            delta = self.review_delta
            value = -float(delta) if delta is not None else None
        return value is None, value or 0, self.index

    def as_json(self) -> dict[str, object]:
        old, new = self.old, self.new
        old_stages, new_stages = old.stages if old else {}, new.stages if new else {}
        stages = []
        for key in sorted(old_stages.keys() | new_stages.keys()):
            left, right = old_stages.get(key), new_stages.get(key)
            stages.append(
                {
                    "stage": key,
                    "old_state": left.state if left else None,
                    "new_state": right.state if right else None,
                    "attempts": _delta(
                        left.attempts if left else None, right.attempts if right else None
                    ),
                    "seconds": _delta(
                        left.seconds if left else None,
                        right.seconds if right else None,
                        comparable=self.same_scope,
                    ),
                }
            )
        return {
            "page_index": self.index,
            "old_status": old.status if old else None,
            "new_status": new.status if new else None,
            "coverage_changed": self.coverage_changed,
            "categories": [
                key for key in FILTERS if key not in ("all", "changed") and self.matches(key)
            ],
            "seconds": _delta(
                old.seconds if old else None,
                new.seconds if new else None,
                comparable=self.same_scope and not self.coverage_changed,
            ),
            "counts": _deltas(old.counts if old else {}, new.counts if new else {}),
            "reasons": _deltas(
                old.reasons if old else {},
                new.reasons if new else {},
                absent_zero=old is not None and new is not None,
            ),
            "stages": stages,
        }


def compare_snapshots(old: Snapshot, new: Snapshot, options: CompareOptions) -> dict[str, object]:
    options.check(old, new)
    same_scope = old.timing_scope == new.timing_scope == TIMING_SCOPE
    same_labels = old.pages.keys() == new.pages.keys() and (old.project_id, old.title) == (
        new.project_id,
        new.title,
    )
    page_comparison = same_labels or options.allow_unmatched
    changes = (
        [
            PageChange(index, old.pages.get(index), new.pages.get(index), same_scope)
            for index in sorted(old.pages.keys() | new.pages.keys())
        ]
        if page_comparison
        else []
    )
    stage_coverage: set[str] = set()
    for change in changes:
        left = change.old.measured_stages if change.old else frozenset()
        right = change.new.measured_stages if change.new else frozenset()
        stage_coverage.update(left ^ right)
    same_coverage = page_comparison and not any(change.coverage_changed for change in changes)
    stages = []
    for key in sorted(old.stages.keys() | new.stages.keys()):
        before, after = old.stages.get(key), new.stages.get(key)
        stages.append(
            {
                "stage": key,
                "counts": _deltas(before.counts if before else {}, after.counts if after else {}),
                "seconds": _delta(
                    before.seconds if before else None,
                    after.seconds if after else None,
                    comparable=page_comparison
                    and same_scope
                    and key not in stage_coverage
                    and before is not None
                    and after is not None
                    and before.counts["timed_jobs"] == after.counts["timed_jobs"],
                ),
            }
        )
    matches = [change for change in changes if change.matches(options.filter)]
    matches.sort(key=lambda change: change.key(options.order))
    end = options.offset + options.limit
    return {
        "schema_version": 1,
        "kind": "processing_report_comparison",
        "baseline": old.header(),
        "candidate": new.header(),
        "same_labels": same_labels,
        "page_comparison": page_comparison,
        "same_timing_scope": same_scope,
        "same_timing_coverage": same_coverage,
        "summary": _deltas(old.counts, new.counts),
        "review": _deltas(old.review, new.review),
        "seconds": _delta(
            old.seconds,
            new.seconds,
            comparable=same_scope and same_coverage and old.timed_jobs == new.timed_jobs,
        ),
        "stages": stages,
        "reasons": _deltas(old.reasons, new.reasons, absent_zero=True),
        "page_counts": {key: sum(change.matches(key) for change in changes) for key in FILTERS},
        "filter": options.filter,
        "order": options.order,
        "offset": options.offset,
        "matched": len(matches),
        "next_offset": end if end < len(matches) else None,
        "pages": [change.as_json() for change in matches[options.offset : end]],
    }
