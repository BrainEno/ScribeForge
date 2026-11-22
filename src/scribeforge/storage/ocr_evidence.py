"""Read-only browser queries over persisted OCR and verification evidence."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Section = Literal["pages", "page", "lines", "blocks", "pairs"]
SECTIONS = ("pages", "page", "lines", "blocks", "pairs")
_MAX_INTEGER = (1 << 63) - 1
_TEXT_LIMIT = 640


@dataclass(frozen=True, slots=True)
class EvidenceQuery:
    project_id: int
    section: str
    page: int | None = None
    run: int | None = None
    after: int = -1
    limit: int = 25

    def __post_init__(self) -> None:
        if self.section not in SECTIONS:
            raise ValueError("unknown evidence section")
        for value, minimum in (
            (self.project_id, 1),
            (self.page, 0),
            (self.run, 1),
            (self.after, -1),
        ):
            if value is not None and not minimum <= value <= _MAX_INTEGER:
                raise ValueError("invalid evidence identifier")
        if not 1 <= self.limit <= 50:
            raise ValueError("evidence limit must be between 1 and 50")
        if (self.page is None) != (self.section == "pages"):
            raise ValueError("a physical page is required except for the page list")
        if (self.run is None) != (self.section not in {"lines", "blocks", "pairs"}):
            raise ValueError("an OCR or verification run is required for lines or pairs")
        if self.section == "page" and self.after != -1:
            raise ValueError("page overview does not support a cursor")


def _batch(rows: list[dict[str, object]], query: EvidenceQuery, key: str) -> dict[str, object]:
    visible = rows[: query.limit]
    return {
        "rows": visible,
        "next_after": visible[-1][key] if len(rows) > query.limit else None,
    }


def _pages(connection: sqlite3.Connection, query: EvidenceQuery) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT p.page_index, p.width, p.height,
               (SELECT COUNT(*) FROM engine_runs e WHERE e.page_id = p.id) AS ocr_runs,
               (SELECT MAX(v.id) FROM verification_runs v WHERE v.page_id = p.id) AS verification
        FROM pages p WHERE p.project_id = ? AND p.page_index > ?
        ORDER BY p.page_index LIMIT ?
        """,
        (query.project_id, query.after, query.limit + 1),
    ).fetchall()
    return _batch([dict(row) for row in rows], query, "page_index")


def _page_overview(connection: sqlite3.Connection, page: sqlite3.Row) -> dict[str, object]:
    page_id = page["id"]
    has_frames = (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ocr_input_frames'"
        ).fetchone()
        is not None
    )
    frame_columns = (
        "f.kind AS frame_kind, f.sha256 AS frame_sha256, "
        "f.width AS frame_width, f.height AS frame_height"
        if has_frames
        else "NULL AS frame_kind, NULL AS frame_sha256, NULL AS frame_width, NULL AS frame_height"
    )
    frame_join = "LEFT JOIN ocr_input_frames f ON f.run_id=e.id" if has_frames else ""
    runs = connection.execute(
        f"""
        SELECT e.id, e.engine, e.engine_version, e.created_at, {frame_columns},
               (SELECT COUNT(*) FROM ocr_lines l WHERE l.run_id=e.id) AS line_count
        FROM engine_runs e {frame_join}
        WHERE e.page_id=? ORDER BY e.id DESC LIMIT 101
        """,
        (page_id,),
    ).fetchall()
    verifications = connection.execute(
        """
        SELECT v.id, v.created_at, v.primary_run_id, v.secondary_run_id,
               (SELECT COUNT(*) FROM alignments a WHERE a.verification_run_id=v.id) AS pair_count
        FROM verification_runs v WHERE v.page_id=? ORDER BY v.id DESC LIMIT 101
        """,
        (page_id,),
    ).fetchall()
    return {
        "width": page["width"],
        "height": page["height"],
        "ocr_runs": [dict(row) for row in runs[:100]],
        "verifications": [dict(row) for row in verifications[:100]],
        "older_ocr_runs_omitted": len(runs) > 100,
        "older_verifications_omitted": len(verifications) > 100,
    }


def _preview(row: sqlite3.Row, field: str) -> dict[str, object]:
    text = str(row[field])
    return {"text": text[:_TEXT_LIMIT], "text_truncated": len(text) > _TEXT_LIMIT}


def _lines(connection: sqlite3.Connection, page_id: int, query: EvidenceQuery) -> dict[str, object]:
    if (
        connection.execute(
            "SELECT 1 FROM engine_runs WHERE id=? AND page_id=?", (query.run, page_id)
        ).fetchone()
        is None
    ):
        raise ValueError("OCR run does not belong to this page")
    rows = connection.execute(
        """
        SELECT l.line_index, l.text, l.x, l.y, l.width, l.height,
               (SELECT COUNT(*) FROM ocr_tokens t WHERE t.line_id=l.id) AS token_count,
               (SELECT MIN(t.confidence) FROM ocr_tokens t WHERE t.line_id=l.id) AS min_confidence
        FROM ocr_lines l WHERE l.run_id=? AND l.line_index > ?
        ORDER BY l.line_index LIMIT ?
        """,
        (query.run, query.after, query.limit + 1),
    ).fetchall()
    return _batch([{**dict(row), **_preview(row, "text")} for row in rows], query, "line_index")


def _blocks(
    connection: sqlite3.Connection, page_id: int, query: EvidenceQuery
) -> dict[str, object]:
    """Page-scoped, cursor-bounded model observations, including empty ranges."""
    if (
        connection.execute(
            "SELECT 1 FROM engine_runs WHERE id=? AND page_id=?", (query.run, page_id)
        ).fetchone()
        is None
    ):
        raise ValueError("OCR run does not belong to this page")
    rows = connection.execute(
        """
        SELECT block_index, block_type, x, y, width, height, line_start, line_end
        FROM ocr_blocks WHERE run_id=? AND block_index > ?
        ORDER BY block_index LIMIT ?
        """,
        (query.run, query.after, query.limit + 1),
    ).fetchall()
    return _batch([dict(row) for row in rows], query, "block_index")


def _members(
    connection: sqlite3.Connection,
    alignment: sqlite3.Row,
    verification: sqlite3.Row,
    side: str,
    *,
    has_members: bool,
) -> dict[str, object]:
    run_id = verification[f"{side}_run_id"]
    if has_members:
        rows = connection.execute(
            """
            SELECT m.line_index, l.text FROM alignment_line_members m
            LEFT JOIN ocr_lines l ON l.line_index=m.line_index AND l.run_id=?
            WHERE m.alignment_id=? AND m.side=? ORDER BY m.member_index LIMIT 9
            """,
            (run_id, alignment["id"], side),
        ).fetchall()
    else:
        rows = []
    legacy = alignment[f"{side}_line_index"]
    if not rows and legacy is not None:
        rows = connection.execute(
            """
            SELECT ? AS line_index, (SELECT text FROM ocr_lines WHERE run_id=? AND line_index=?) AS text
            """,
            (legacy, run_id, legacy),
        ).fetchall()
    return {
        "lines": [
            {
                "line_index": row["line_index"],
                **(
                    {"text": None, "text_truncated": False}
                    if row["text"] is None
                    else _preview(row, "text")
                ),
            }
            for row in rows[:8]
        ],
        "additional_members": len(rows) > 8,
    }


def _pairs(connection: sqlite3.Connection, page_id: int, query: EvidenceQuery) -> dict[str, object]:
    verification = connection.execute(
        """
        SELECT v.primary_run_id, v.secondary_run_id FROM verification_runs v
        JOIN engine_runs p ON p.id=v.primary_run_id AND p.page_id=v.page_id
        JOIN engine_runs s ON s.id=v.secondary_run_id AND s.page_id=v.page_id
        WHERE v.id=? AND v.page_id=?
        """,
        (query.run, page_id),
    ).fetchone()
    if verification is None:
        raise ValueError("verification and its OCR runs must belong to this page")
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('alignment_line_members', 'alignment_ambiguities')"
        )
    }
    ambiguity = (
        "EXISTS(SELECT 1 FROM alignment_ambiguities b WHERE b.alignment_id=a.id)"
        if "alignment_ambiguities" in tables
        else "0"
    )
    rows = connection.execute(
        f"""
        SELECT a.id, a.pair_index, a.primary_line_index, a.secondary_line_index,
               a.text_similarity, a.vertical_distance, r.score AS risk_score,
               {ambiguity} AS ambiguous,
               (SELECT COUNT(*) FROM alignment_conflicts c WHERE c.alignment_id=a.id) AS conflicts
        FROM alignments a LEFT JOIN risks r ON r.alignment_id=a.id
        WHERE a.verification_run_id=? AND a.pair_index>? ORDER BY a.pair_index LIMIT ?
        """,
        (query.run, query.after, query.limit + 1),
    ).fetchall()
    batch = _batch([dict(row) for row in rows], query, "pair_index")
    visible: list[dict[str, object]] = []
    for row in rows[: query.limit]:
        result = {
            key: row[key]
            for key in (
                "pair_index",
                "text_similarity",
                "vertical_distance",
                "risk_score",
                "conflicts",
            )
        }
        result["ambiguous"] = bool(row["ambiguous"])
        for side in ("primary", "secondary"):
            result[side] = _members(
                connection, row, verification, side, has_members="alignment_line_members" in tables
            )
        visible.append(result)
    batch["rows"] = visible
    return batch


def read_ocr_evidence(database: Path, query: EvidenceQuery) -> dict[str, object]:
    """Query existing rows in a single snapshot; do not initialize or migrate."""
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        project = connection.execute(
            "SELECT title FROM projects WHERE id=?", (query.project_id,)
        ).fetchone()
        if project is None:
            raise ValueError("project was not found")
        if query.section == "pages":
            result = _pages(connection, query)
        else:
            page = connection.execute(
                "SELECT id, width, height FROM pages WHERE project_id=? AND page_index=?",
                (query.project_id, query.page),
            ).fetchone()
            if page is None:
                raise ValueError("physical page was not found in this project")
            if query.section == "page":
                result = _page_overview(connection, page)
            elif query.section == "lines":
                result = _lines(connection, int(page["id"]), query)
            elif query.section == "blocks":
                result = _blocks(connection, int(page["id"]), query)
            else:
                result = _pairs(connection, int(page["id"]), query)
        return {
            "schema_version": 1,
            "project_id": query.project_id,
            "project_title": project["title"],
            "section": query.section,
            "page_index": query.page,
            "run_id": query.run,
            **result,
        }
