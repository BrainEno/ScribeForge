"""Read-only, bounded history of recorded job failures and interrupted attempts."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from scribeforge.domain.jobs import JobStage
from scribeforge.storage.sqlite import SQLiteStore

_MAX_INT = (1 << 63) - 1
_PREVIEW = 4096


@dataclass(frozen=True, slots=True)
class JobHistoryQuery:
    project_id: int | None = None
    page_index: int | None = None
    stage: str | None = None
    before: int | None = None
    limit: int = 25

    def __post_init__(self) -> None:
        for value, minimum in ((self.project_id, 1), (self.page_index, 0), (self.before, 1)):
            if value is not None and (isinstance(value, bool) or not minimum <= value <= _MAX_INT):
                raise ValueError("invalid project, page or cursor")
        if isinstance(self.limit, bool) or not 1 <= self.limit <= 100:
            raise ValueError("history limit must be between 1 and 100")
        if self.stage is not None and self.stage not in {stage.value for stage in JobStage}:
            raise ValueError("unknown job stage")


def read_job_history(database: Path, query: JobHistoryQuery) -> dict[str, object]:
    """Read a descending, exclusive-cursor page without initializing the store.

    Histories record failure and lost ownership, not all attempts. A pre-v19
    store has no history table: return an explicit unavailable state, never
    fabricate attempts or migrate a project as a side effect of inspection.
    Error previews may still contain private information despite redaction.
    """
    if not database.is_file():
        raise ValueError("project store not found")
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version > SQLiteStore.SCHEMA_VERSION:
            raise ValueError(f"unsupported database schema version: {version}")
        projects = db.execute("SELECT id FROM projects ORDER BY id").fetchall()
        if query.project_id is None:
            if len(projects) != 1:
                raise ValueError("choose a project ID in a multi-project or empty store")
            project_id = int(projects[0]["id"])
        else:
            project_id = query.project_id
            if not any(int(row["id"]) == project_id for row in projects):
                raise ValueError("project ID not found")
        if query.page_index is not None:
            found = db.execute(
                "SELECT 1 FROM pages WHERE project_id=? AND page_index=?",
                (project_id, query.page_index),
            ).fetchone()
            if found is None:
                raise ValueError("physical page not found in project")
        if version < 19:
            return {
                "schema_version": 1,
                "project_id": project_id,
                "history_available": False,
                "rows": [],
                "next_before": None,
                "notice": "No attempt history was recorded by this project version; current errors may still appear in status.",
            }
        cursor = _MAX_INT if query.before is None else query.before
        rows = db.execute(
            """
            SELECT h.id, h.job_id, h.attempt, h.outcome, h.started_at, h.finished_at,
                   h.recorded_at, h.origin, substr(h.error, 1, ?) AS error_preview,
                   length(h.error) AS error_length, j.stage, j.state AS current_state,
                   p.page_index
            FROM job_attempt_history h JOIN jobs j ON j.id = h.job_id
            LEFT JOIN pages p ON p.id = j.page_id
            WHERE j.project_id=? AND h.id < ?
              AND (? IS NULL OR p.page_index=?)
              AND (? IS NULL OR j.stage=?)
            ORDER BY h.id DESC LIMIT ?
            """,
            (
                _PREVIEW,
                project_id,
                cursor,
                query.page_index,
                query.page_index,
                query.stage,
                query.stage,
                query.limit + 1,
            ),
        ).fetchall()
        visible = rows[: query.limit]
        result = []
        for row in visible:
            item = dict(row)
            length = item.pop("error_length")
            item["error_truncated"] = length is not None and int(length) > _PREVIEW
            result.append(item)
        return {
            "schema_version": 1,
            "project_id": project_id,
            "history_available": True,
            "rows": result,
            "next_before": visible[-1]["id"] if len(rows) > query.limit else None,
            "notice": "Private diagnostic evidence; failed attempts and recovered running attempts only. Older failures may already be lost.",
        }
