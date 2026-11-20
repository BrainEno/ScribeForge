"""Project-local literal search over persisted OCR lines or decision records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SOURCES = ("ocr", "decisions")
ACTORS = ("human", "automatic", "vlm")
MAX_QUERY = 160
MAX_PAGE_SIZE = 50
CONTEXT = 72
DETAIL_LIMIT = 12000
MAX_INTEGER = (1 << 63) - 1
Scope = Literal["ocr", "decisions"]


@dataclass(frozen=True, slots=True)
class EvidenceSearchQuery:
    project_id: int
    source: str
    term: str
    page_start: int | None = None
    page_end: int | None = None
    actor: str | None = None
    snapshot_id: int | None = None
    before_id: int | None = None
    record_id: int | None = None
    limit: int = 20

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError("search source must be ocr or decisions")
        if not self.term.strip() or len(self.term) > MAX_QUERY or "\x00" in self.term:
            raise ValueError(f"search text must contain 1 to {MAX_QUERY} characters without NUL")
        for name, value, floor in (
            ("project", self.project_id, 1),
            ("start page", self.page_start, 0),
            ("end page", self.page_end, 0),
            ("snapshot", self.snapshot_id, 0),
            ("before", self.before_id, 1),
            ("record", self.record_id, 1),
        ):
            if value is not None and not floor <= value <= MAX_INTEGER:
                raise ValueError(f"invalid {name} value")
        if (
            self.page_start is not None
            and self.page_end is not None
            and self.page_start > self.page_end
        ):
            raise ValueError("start page is after end page")
        if self.actor is not None and (self.source != "decisions" or self.actor not in ACTORS):
            raise ValueError("actor filter is only available for saved decisions")
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"search limit must be between 1 and {MAX_PAGE_SIZE}")
        if self.before_id is not None and self.snapshot_id is None:
            raise ValueError("pagination requires a snapshot id")
        if self.record_id is not None and (self.snapshot_id is None or self.before_id is not None):
            raise ValueError("record detail requires a snapshot id and no cursor")


@dataclass(frozen=True, slots=True)
class _SourceSql:
    table: str
    joins: str
    text: str
    evidence: str


_OCR = _SourceSql(
    "ocr_lines",
    "JOIN engine_runs e ON e.id=r.run_id JOIN pages p ON p.id=e.page_id",
    "r.text",
    "r.run_id, r.line_index, e.engine, e.engine_version, r.x, r.y, r.width, r.height",
)
_DECISIONS = _SourceSql(
    "decisions",
    "JOIN pages p ON p.id=r.page_id",
    "r.selected_text",
    """r.pair_index, r.actor, r.created_at, r.evidence_refs_json,
       (SELECT MAX(d.id) FROM decisions d WHERE d.page_id=r.page_id
                    AND d.pair_index=r.pair_index AND d.id <= ?) AS latest_for_pair""",
)


def _project_maximum(connection: sqlite3.Connection, sql: _SourceSql, project_id: int) -> int:
    row = connection.execute(
        f"SELECT MAX(r.id) FROM {sql.table} r {sql.joins} WHERE p.project_id=?",
        (project_id,),
    ).fetchone()
    return int(row[0] or 0)


def _matches(
    connection: sqlite3.Connection, query: EvidenceSearchQuery, sql: _SourceSql, snapshot: int
) -> list[sqlite3.Row]:
    conditions = ["p.project_id=?", "r.id<=?", f"instr({sql.text}, ?) > 0"]
    params: list[int | str] = [query.project_id, snapshot, query.term]
    for clause, value in (
        ("p.page_index>=?", query.page_start),
        ("p.page_index<=?", query.page_end),
        ("r.actor=?", query.actor),
        ("r.id<?", query.before_id),
        ("r.id=?", query.record_id),
    ):
        if value is not None:
            conditions.append(clause)
            params.append(value)
    prefix: tuple[int, ...] = (snapshot,) if sql is _DECISIONS else ()
    # SQL fragments are internal constants. User text is always bound and instr
    # treats %, _, quotes and regular-expression syntax as literal characters.
    return connection.execute(
        f"""
        SELECT r.id, p.page_index, p.id AS page_id, {sql.text} AS original_text, {sql.evidence}
        FROM {sql.table} r {sql.joins}
        WHERE {" AND ".join(conditions)} ORDER BY r.id DESC LIMIT ?
        """,
        (*prefix, *params, query.limit + 1),
    ).fetchall()


def _excerpt(text: str, term: str, *, detail: bool) -> dict[str, object]:
    # Offsets are represented as substrings, not byte offsets or UTF-16 units.
    # SQLite and Python agree on literal matching, including supplementary CJK.
    match = text.index(term)
    end = match + len(term)
    if detail:
        before_budget = (DETAIL_LIMIT - len(term)) // 2
        start = max(0, match - before_budget)
        stop = min(len(text), start + DETAIL_LIMIT)
        start = max(0, stop - DETAIL_LIMIT)
    else:
        start, stop = max(0, match - CONTEXT), min(len(text), end + CONTEXT)
    return {
        "before": text[start:match],
        "match": text[match:end],
        "after": text[end:stop],
        "omitted_before": start > 0,
        "omitted_after": stop < len(text),
    }


def _citations(value: object) -> list[str] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except ValueError:
        return None
    if not isinstance(decoded, list) or any(not isinstance(ref, str) for ref in decoded):
        return None
    return decoded


def _verification_ref(refs: list[str] | None) -> int | None:
    if refs is None:
        return None
    tag = "verification_run:"
    values = [ref.removeprefix(tag) for ref in refs if ref.startswith(tag)]
    if (
        len(values) != 1
        or not values[0].isascii()
        or not values[0].isdigit()
        or len(values[0]) > 19
    ):
        return None
    value = int(values[0])
    return value if 0 < value <= MAX_INTEGER and str(value) == values[0] else None


def _decision_fields(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    refs = _citations(row["evidence_refs_json"])
    run = _verification_ref(refs)
    latest_run = connection.execute(
        "SELECT MAX(id) FROM verification_runs WHERE page_id=?", (row["page_id"],)
    ).fetchone()[0]
    linked = (
        run is not None
        and connection.execute(
            """
        SELECT 1 FROM verification_runs v JOIN alignments a ON a.verification_run_id=v.id
        WHERE v.id=? AND v.page_id=? AND a.pair_index=?
        """,
            (run, row["page_id"], row["pair_index"]),
        ).fetchone()
        is not None
    )
    return {
        "pair_index": row["pair_index"],
        "actor": row["actor"],
        "created_at": row["created_at"],
        "evidence_refs": [] if refs is None else refs[:24],
        "citations_incomplete": refs is None or len(refs) > 24,
        "verification_id": run,
        "binding": ("current" if run == latest_run else "historical") if linked else "unverified",
        "latest_for_pair": row["id"] == row["latest_for_pair"],
    }


def _ocr_fields(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    # A run may be an extra opinion or a failed attempt, not one of the two
    # engines in the latest verification. Report that distinction explicitly.
    latest = connection.execute(
        """
        SELECT primary_run_id, secondary_run_id FROM verification_runs
        WHERE page_id=? ORDER BY id DESC LIMIT 1
        """,
        (row["page_id"],),
    ).fetchone()
    role = "unverified"
    if latest is not None:
        if row["run_id"] == latest["primary_run_id"]:
            role = "primary"
        elif row["run_id"] == latest["secondary_run_id"]:
            role = "secondary"
        else:
            role = "other"
    return {
        "run_id": row["run_id"],
        "line_index": row["line_index"],
        "engine": row["engine"],
        "engine_version": row["engine_version"],
        "role_in_latest_verification": role,
        "box": [row[key] for key in ("x", "y", "width", "height")],
    }


def read_evidence_search(database: Path, query: EvidenceSearchQuery) -> dict[str, object]:
    """Search in one read transaction; cursors pin the upper result ID bound.

    New records cannot enter an existing result sequence. Verification labels
    are evaluated at read time, and never used to choose an export decision.
    """
    sql = _OCR if query.source == "ocr" else _DECISIONS
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        project = connection.execute(
            "SELECT title FROM projects WHERE id=?", (query.project_id,)
        ).fetchone()
        if project is None:
            raise ValueError("project was not found")
        maximum = _project_maximum(connection, sql, query.project_id)
        if query.snapshot_id is not None and query.snapshot_id > maximum:
            raise ValueError("search snapshot is no longer available; repeat the search")
        snapshot = maximum if query.snapshot_id is None else query.snapshot_id
        rows = _matches(connection, query, sql, snapshot)
        if query.record_id is not None and not rows:
            raise ValueError("record was not found in this search snapshot")
        hits = []
        for row in rows[: query.limit]:
            evidence = (
                _ocr_fields(connection, row) if sql is _OCR else _decision_fields(connection, row)
            )
            hits.append(
                {
                    "id": row["id"],
                    "page_index": row["page_index"],
                    "excerpt": _excerpt(
                        row["original_text"], query.term, detail=query.record_id is not None
                    ),
                    **evidence,
                }
            )
        return {
            "schema_version": 1,
            "project_id": query.project_id,
            "project_title": project["title"],
            "source": query.source,
            "term": query.term,
            "snapshot_id": snapshot,
            "next_before": rows[query.limit - 1]["id"] if len(rows) > query.limit else None,
            "hits": hits,
        }
