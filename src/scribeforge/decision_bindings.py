"""Read-only checks for decisions against a page's active verification."""

from __future__ import annotations

import json
import sqlite3


def inspect_latest_decisions(
    connection: sqlite3.Connection, page_id: int, verification_id: int, *, multiple_runs: bool
) -> tuple[tuple[sqlite3.Row, str], ...]:
    """Return latest decisions that cannot safely be applied to this run.

    Missing citations in a single run are unknown, not inferred. All other
    invalid bindings are ambiguous. Audit and selection share these rules.
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
    findings: list[tuple[sqlite3.Row, str]] = []
    for decision in decisions:
        try:
            decoded = json.loads(str(decision["evidence_refs_json"]))
            if not isinstance(decoded, list):
                raise TypeError("stored JSON list is invalid")
            refs = [str(ref) for ref in decoded if str(ref).startswith("verification_run:")]
        except (ValueError, TypeError):
            findings.append((decision, "ambiguous"))
            continue
        if decision["alignment_id"] is None:
            findings.append((decision, "ambiguous"))
        elif not refs and not multiple_runs:
            findings.append((decision, "unknown"))
        elif refs != [f"verification_run:{verification_id}"]:
            findings.append((decision, "ambiguous"))
    return tuple(findings)
