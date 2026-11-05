"""Read-only checks for decisions against a page's active verification."""

from __future__ import annotations

import json
import sqlite3


def ambiguous_latest_decisions(
    connection: sqlite3.Connection, page_id: int, verification_id: int
) -> tuple[sqlite3.Row, ...]:
    """Return latest decisions that cannot safely be applied to this run.

    The caller must already have determined that the page has multiple runs.
    Rows are returned so audit and active-context selection use identical rules.
    """
    decisions = connection.execute(
        """
        SELECT d.id, d.pair_index, d.evidence_refs_json, a.id AS alignment_id
        FROM decisions AS d
        LEFT JOIN alignments AS a
          ON a.verification_run_id = ? AND a.pair_index = d.pair_index
        WHERE d.page_id = ? AND d.id = (
            SELECT MAX(newer.id) FROM decisions AS newer
            WHERE newer.page_id = d.page_id AND newer.pair_index = d.pair_index
        )
        ORDER BY d.pair_index
        """,
        (verification_id, page_id),
    ).fetchall()
    ambiguous: list[sqlite3.Row] = []
    for decision in decisions:
        try:
            decoded = json.loads(str(decision["evidence_refs_json"]))
            if not isinstance(decoded, list):
                raise TypeError("stored JSON list is invalid")
            refs = [str(ref) for ref in decoded if str(ref).startswith("verification_run:")]
        except (ValueError, TypeError):
            refs = []
        if decision["alignment_id"] is None or refs != [f"verification_run:{verification_id}"]:
            ambiguous.append(decision)
    return tuple(ambiguous)
