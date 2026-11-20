"""Read-only access to the append-only review journal (not an export gate)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MAX_HISTORY_PAGE_SIZE = 100
PREVIEW_LENGTH = 320
DETAIL_LENGTH = 16000
MAX_ID = (1 << 63) - 1
ACTORS = ("human", "automatic", "vlm")
Binding = Literal["current", "historical", "unverified"]


@dataclass(frozen=True, slots=True)
class HistoryQuery:
    project_id: int
    page: int | None = None
    pair: int | None = None
    actor: str | None = None
    snapshot_id: int | None = None
    before_id: int | None = None
    decision_id: int | None = None
    limit: int = 30

    def __post_init__(self) -> None:
        for field, value, floor in (
            ("project", self.project_id, 1),
            ("page", self.page, 0),
            ("pair", self.pair, 0),
            ("snapshot", self.snapshot_id, 0),
            ("before", self.before_id, 1),
            ("decision", self.decision_id, 1),
        ):
            if value is not None and not floor <= value <= MAX_ID:
                raise ValueError(f"invalid {field} index")
        if self.pair is not None and self.page is None:
            raise ValueError("pair filter requires a page filter")
        if self.actor is not None and self.actor not in ACTORS:
            raise ValueError("unrecognized decision actor")
        if not 1 <= self.limit <= MAX_HISTORY_PAGE_SIZE:
            raise ValueError(f"history limit must be between 1 and {MAX_HISTORY_PAGE_SIZE}")
        if self.before_id is not None and self.snapshot_id is None:
            raise ValueError("pagination requires a snapshot id")
        if self.decision_id is not None and self.before_id is not None:
            raise ValueError("a detail request cannot be paginated")


def _string_list(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, str):
        return None
    try:
        decoded = json.loads(value)
    except ValueError:
        return None
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        return None
    return tuple(decoded)


def _run_reference(refs: tuple[str, ...] | None) -> int | None:
    if refs is None:
        return None
    prefix = "verification_run:"
    tagged = [ref.removeprefix(prefix) for ref in refs if ref.startswith(prefix)]
    if (
        len(tagged) != 1
        or len(tagged[0]) > 19
        or not tagged[0].isascii()
        or not tagged[0].isdigit()
    ):
        return None
    value = int(tagged[0])
    return value if 0 < value <= MAX_ID and str(value) == tagged[0] else None


def _binding(connection: sqlite3.Connection, row: sqlite3.Row, run: int | None) -> Binding:
    if run is None:
        return "unverified"
    linked = connection.execute(
        """
        SELECT 1 FROM verification_runs v JOIN alignments a ON a.verification_run_id = v.id
        WHERE v.id = ? AND v.page_id = ? AND a.pair_index = ?
        """,
        (run, row["page_id"], row["pair_index"]),
    ).fetchone()
    if linked is None:
        return "unverified"
    return "current" if row["current_run"] == run else "historical"


def _as_record(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, object]:
    refs = _string_list(row["evidence_refs_json"])
    run = _run_reference(refs)
    reasons = _string_list(row["risk_reasons"])
    text = str(row["selected_text"])
    preview = int(row["preview_limit"])
    return {
        "id": row["id"],
        "page_index": row["page_index"],
        "pair_index": row["pair_index"],
        "actor": row["actor"],
        "created_at": row["created_at"],
        "text": text[:preview],
        "text_truncated": len(text) > preview,
        "evidence_refs": list(refs or ()),
        "citation_invalid": refs is None,
        "verification_id": run,
        "current_verification_id": row["current_run"],
        "binding": _binding(connection, row, run),
        "latest_for_pair": row["latest_id"] == row["id"],
        "previous_decision_id": row["previous_id"],
        "risk_score": row["risk_score"],
        "risk_reasons": list(reasons or ()),
    }


def _history_rows(
    connection: sqlite3.Connection, query: HistoryQuery, snapshot: int
) -> list[sqlite3.Row]:
    conditions = ["p.project_id = ?", "d.id <= ?"]
    params: list[int | str] = [query.project_id, snapshot]
    for clause, value in (
        ("p.page_index = ?", query.page),
        ("d.pair_index = ?", query.pair),
        ("d.actor = ?", query.actor),
        ("d.id < ?", query.before_id),
        ("d.id = ?", query.decision_id),
    ):
        if value is not None:
            conditions.append(clause)
            params.append(value)
    preview = DETAIL_LENGTH if query.decision_id is not None else PREVIEW_LENGTH
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(decisions)")}
    score = "d.risk_score" if "risk_score" in columns else "NULL"
    reasons = "d.risk_reasons_json" if "risk_reasons_json" in columns else "'[]'"
    # All interpolated SQL is fixed syntax, selected from this module; user
    # values are bound parameters. The project scope also applies to details.
    rows = connection.execute(
        f"""
        SELECT d.id, d.page_id, p.page_index, d.pair_index, d.actor, d.created_at,
               d.selected_text, ? AS preview_limit,
               d.evidence_refs_json, {score} AS risk_score, {reasons} AS risk_reasons,
               (SELECT MAX(v.id) FROM verification_runs v WHERE v.page_id = d.page_id) AS current_run,
               (SELECT MAX(x.id) FROM decisions x WHERE x.page_id=d.page_id
                   AND x.pair_index=d.pair_index AND x.id<=?) AS latest_id,
               (SELECT MAX(x.id) FROM decisions x WHERE x.page_id=d.page_id
                   AND x.pair_index=d.pair_index AND x.id<d.id) AS previous_id
        FROM decisions d JOIN pages p ON d.page_id=p.id
        WHERE {" AND ".join(conditions)} ORDER BY d.id DESC LIMIT ?
        """,
        (preview, snapshot, *params, query.limit + 1),
    ).fetchall()
    return rows


def read_review_history(database: Path, query: HistoryQuery) -> dict[str, object]:
    """Read a bounded batch without initializing/migrating a project database.

    Snapshot IDs pin the decision upper bound between requests, including when
    actor or page filters hide newer revisions. The active verification label
    describes the database at the time of each read, not an earlier export.
    """
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        project = connection.execute(
            "SELECT id, title FROM projects WHERE id = ?", (query.project_id,)
        ).fetchone()
        if project is None:
            raise ValueError(f"project {query.project_id} was not found")
        newest = connection.execute(
            "SELECT MAX(d.id) FROM decisions d JOIN pages p ON p.id=d.page_id WHERE p.project_id = ?",
            (query.project_id,),
        ).fetchone()[0]
        maximum = int(newest or 0)
        snapshot = maximum if query.snapshot_id is None else min(query.snapshot_id, maximum)
        rows = _history_rows(connection, query, snapshot)
        if query.decision_id is not None and not rows:
            raise ValueError(f"decision {query.decision_id} was not found in this history")
        returned = rows[: query.limit]
        return {
            "schema_version": 1,
            "project_id": project["id"],
            "project_title": project["title"],
            "snapshot_id": snapshot,
            "next_before_id": returned[-1]["id"] if len(rows) > len(returned) else None,
            "decisions": [_as_record(connection, row) for row in returned],
        }
