from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from scribeforge.domain.alignment import (
    AlignmentConflict,
    ConflictKind,
    LineAlignment,
    PageAlignment,
)
from scribeforge.domain.jobs import JobRecord, JobStage, JobState
from scribeforge.domain.ocr import (
    BlockType,
    BoundingBox,
    OCRBlock,
    OCRLine,
    OCRToken,
    PageDirection,
    PageOCRResult,
)
from scribeforge.domain.risk import PairRisk, ReviewCandidate, RiskReason
from scribeforge.domain.structure import (
    BookStructure,
    Chapter,
    NumberingScheme,
    PageLabel,
    PageRole,
    PageStructure,
)
from scribeforge.domain.vlm import VLMReading


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    id: int
    project_id: int
    page_index: int
    pair_index: int
    selected_text: str
    actor: str
    evidence_refs: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: int
    title: str
    source_uri: str
    source_sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class PageRecord:
    id: int
    project_id: int
    page_index: int
    source_image: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class PreprocessRecord:
    """Stored preprocessing evidence for one page."""

    page_index: int
    settings: dict[str, Any]
    applied: tuple[str, ...]
    measured_skew_degrees: float
    output_image: str
    output_sha256: str
    output_width: int
    output_height: int
    notes: dict[str, Any]
    created_at: str


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    id: int
    primary_run_id: int
    secondary_run_id: int
    alignment: PageAlignment
    risks: tuple[PairRisk, ...]
    candidates: tuple[ReviewCandidate, ...]


@dataclass(frozen=True, slots=True)
class VLMReviewRecord:
    id: int
    verification_run_id: int
    pair_index: int
    crop_path: str
    crop_sha256: str
    reading: VLMReading
    created_at: str


class SQLiteStore:
    SCHEMA_VERSION = 7

    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > self.SCHEMA_VERSION:
                raise RuntimeError(f"unsupported database schema version: {version}")
            self._create_v1(connection)
            if version < 1:
                connection.execute("PRAGMA user_version = 1")
            if version < 2:
                self._create_v2(connection)
                connection.execute("PRAGMA user_version = 2")
            if version < 3:
                self._create_v3(connection)
                connection.execute("PRAGMA user_version = 3")
            if version < 4:
                self._create_v4(connection)
                connection.execute("PRAGMA user_version = 4")
            if version < 5:
                self._create_v5(connection)
                connection.execute("PRAGMA user_version = 5")
            if version < 6:
                self._create_v6(connection)
                connection.execute("PRAGMA user_version = 6")
            if version < 7:
                self._create_v7(connection)
                connection.execute("PRAGMA user_version = 7")

    @staticmethod
    def _create_v1(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
                page_index INTEGER NOT NULL CHECK(page_index >= 0),
                source_image TEXT NOT NULL,
                width INTEGER NOT NULL CHECK(width > 0),
                height INTEGER NOT NULL CHECK(height > 0),
                UNIQUE(project_id, page_index)
            );

            CREATE TABLE IF NOT EXISTS engine_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE RESTRICT,
                engine TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ocr_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES engine_runs(id) ON DELETE RESTRICT,
                line_index INTEGER NOT NULL CHECK(line_index >= 0),
                text TEXT NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                width REAL NOT NULL,
                height REAL NOT NULL,
                UNIQUE(run_id, line_index)
            );

            CREATE TABLE IF NOT EXISTS ocr_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                line_id INTEGER NOT NULL REFERENCES ocr_lines(id) ON DELETE RESTRICT,
                token_index INTEGER NOT NULL CHECK(token_index >= 0),
                text TEXT NOT NULL,
                confidence REAL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                width REAL NOT NULL,
                height REAL NOT NULL,
                UNIQUE(line_id, token_index)
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE RESTRICT,
                pair_index INTEGER NOT NULL CHECK(pair_index >= 0),
                selected_text TEXT NOT NULL,
                actor TEXT NOT NULL CHECK(actor IN ('automatic', 'vlm', 'human')),
                evidence_refs_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_engine_runs_page ON engine_runs(page_id);
            CREATE INDEX IF NOT EXISTS idx_decisions_page_pair
                ON decisions(page_id, pair_index, id);
            """
        )

    @staticmethod
    def _create_v2(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS verification_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE RESTRICT,
                primary_run_id INTEGER NOT NULL
                    REFERENCES engine_runs(id) ON DELETE RESTRICT,
                secondary_run_id INTEGER NOT NULL
                    REFERENCES engine_runs(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verification_run_id INTEGER NOT NULL
                    REFERENCES verification_runs(id) ON DELETE RESTRICT,
                pair_index INTEGER NOT NULL CHECK(pair_index >= 0),
                primary_line_index INTEGER,
                secondary_line_index INTEGER,
                text_similarity REAL NOT NULL,
                vertical_distance REAL,
                UNIQUE(verification_run_id, pair_index)
            );

            CREATE TABLE IF NOT EXISTS alignment_conflicts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alignment_id INTEGER NOT NULL REFERENCES alignments(id) ON DELETE RESTRICT,
                conflict_index INTEGER NOT NULL CHECK(conflict_index >= 0),
                kind TEXT NOT NULL,
                primary_text TEXT NOT NULL,
                secondary_text TEXT NOT NULL,
                primary_start INTEGER NOT NULL,
                primary_end INTEGER NOT NULL,
                secondary_start INTEGER NOT NULL,
                secondary_end INTEGER NOT NULL,
                UNIQUE(alignment_id, conflict_index)
            );

            CREATE TABLE IF NOT EXISTS risks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alignment_id INTEGER NOT NULL UNIQUE
                    REFERENCES alignments(id) ON DELETE RESTRICT,
                score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
                reasons_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS review_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alignment_id INTEGER NOT NULL REFERENCES alignments(id) ON DELETE RESTRICT,
                score REAL NOT NULL CHECK(score >= 0 AND score <= 1),
                reasons_json TEXT NOT NULL,
                crop_x REAL NOT NULL,
                crop_y REAL NOT NULL,
                crop_width REAL NOT NULL,
                crop_height REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_verification_runs_page
                ON verification_runs(page_id, id);
            CREATE INDEX IF NOT EXISTS idx_review_candidates_alignment
                ON review_candidates(alignment_id, id);
            """
        )

    @staticmethod
    def _create_v3(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS vlm_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                review_candidate_id INTEGER NOT NULL
                    REFERENCES review_candidates(id) ON DELETE RESTRICT,
                model TEXT NOT NULL,
                model_version TEXT NOT NULL,
                text TEXT NOT NULL,
                uncertain INTEGER NOT NULL CHECK(uncertain IN (0, 1)),
                crop_path TEXT NOT NULL,
                crop_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_vlm_reviews_candidate
                ON vlm_reviews(review_candidate_id, id);
            """
        )

    @staticmethod
    def _create_v4(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
                page_id INTEGER REFERENCES pages(id) ON DELETE RESTRICT,
                stage TEXT NOT NULL CHECK(
                    stage IN ('import', 'primary_ocr', 'secondary_ocr', 'verify', 'vlm_review')
                ),
                state TEXT NOT NULL DEFAULT 'pending' CHECK(
                    state IN ('pending', 'running', 'succeeded', 'failed')
                ),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT,
                CHECK(
                    (stage = 'import' AND page_id IS NULL)
                    OR
                    (stage != 'import' AND page_id IS NOT NULL)
                )
            );

            CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_scope_stage
                ON jobs(project_id, stage, COALESCE(page_id, -1));
            CREATE INDEX IF NOT EXISTS idx_jobs_resumable
                ON jobs(project_id, state, id);
            """
        )

    @staticmethod
    def _create_v5(connection: sqlite3.Connection) -> None:
        """Add the preprocessing stage and its per-page evidence.

        SQLite cannot alter a CHECK constraint, and the v4 ``jobs.stage`` check
        lists every allowed stage literally. Adding ``preprocess`` therefore
        requires rebuilding the table: create the new shape, copy the rows,
        drop the old table, rename, and recreate the indexes.
        """

        connection.executescript(
            """
            CREATE TABLE jobs_v5 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
                page_id INTEGER REFERENCES pages(id) ON DELETE RESTRICT,
                stage TEXT NOT NULL CHECK(
                    stage IN (
                        'import', 'preprocess', 'primary_ocr',
                        'secondary_ocr', 'verify', 'vlm_review'
                    )
                ),
                state TEXT NOT NULL DEFAULT 'pending' CHECK(
                    state IN ('pending', 'running', 'succeeded', 'failed')
                ),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT,
                CHECK(
                    (stage = 'import' AND page_id IS NULL)
                    OR
                    (stage != 'import' AND page_id IS NOT NULL)
                )
            );

            INSERT INTO jobs_v5 (
                id, project_id, page_id, stage, state, attempts, last_error,
                created_at, started_at, finished_at
            )
            SELECT
                id, project_id, page_id, stage, state, attempts, last_error,
                created_at, started_at, finished_at
            FROM jobs;

            DROP TABLE jobs;
            ALTER TABLE jobs_v5 RENAME TO jobs;

            CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_scope_stage
                ON jobs(project_id, stage, COALESCE(page_id, -1));
            CREATE INDEX IF NOT EXISTS idx_jobs_resumable
                ON jobs(project_id, state, id);

            CREATE TABLE IF NOT EXISTS page_preprocess (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id INTEGER NOT NULL UNIQUE
                    REFERENCES pages(id) ON DELETE RESTRICT,
                settings_json TEXT NOT NULL,
                applied_json TEXT NOT NULL,
                measured_skew_degrees REAL NOT NULL,
                output_image TEXT NOT NULL,
                output_sha256 TEXT NOT NULL,
                output_width INTEGER NOT NULL CHECK(output_width > 0),
                output_height INTEGER NOT NULL CHECK(output_height > 0),
                notes_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    @staticmethod
    def _create_v6(connection: sqlite3.Connection) -> None:
        """Add page roles, printed page labels and the chapter tree.

        Printed page numbers are stored beside the physical ``page_index`` rather
        than replacing it: the physical index is what every OCR, risk and decision
        row references, while the printed label is what a reader or a citation
        uses. This book restarts its numbering per section, so a label needs the
        numbering run it belongs to in order to stay unambiguous.
        """

        connection.executescript(
            """
            ALTER TABLE engine_runs ADD COLUMN direction TEXT;
            ALTER TABLE ocr_lines ADD COLUMN confidence REAL;

            CREATE TABLE IF NOT EXISTS ocr_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES engine_runs(id) ON DELETE RESTRICT,
                block_index INTEGER NOT NULL CHECK(block_index >= 0),
                block_type TEXT NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                width REAL NOT NULL,
                height REAL NOT NULL,
                line_start INTEGER NOT NULL CHECK(line_start >= 0),
                line_end INTEGER NOT NULL CHECK(line_end > line_start),
                UNIQUE(run_id, block_index)
            );

            CREATE INDEX IF NOT EXISTS idx_ocr_blocks_run
                ON ocr_blocks(run_id, block_index);

            CREATE TABLE IF NOT EXISTS page_structure (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id INTEGER NOT NULL UNIQUE
                    REFERENCES pages(id) ON DELETE RESTRICT,
                role TEXT NOT NULL CHECK(
                    role IN (
                        'cover', 'front_matter', 'copyright', 'table_of_contents',
                        'body', 'plate', 'blank', 'back_matter', 'unknown'
                    )
                ),
                printed_label TEXT NOT NULL DEFAULT '',
                numbering_scheme TEXT NOT NULL DEFAULT 'none' CHECK(
                    numbering_scheme IN (
                        'none', 'arabic', 'roman_lower', 'roman_upper', 'cjk', 'unknown'
                    )
                ),
                sequence_number INTEGER CHECK(
                    sequence_number IS NULL OR sequence_number > 0
                ),
                sequence_ordinal INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CHECK(
                    (numbering_scheme = 'none' AND printed_label = '')
                    OR (numbering_scheme != 'none' AND printed_label != '')
                )
            );

            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
                ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                level INTEGER NOT NULL CHECK(level >= 1),
                title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                start_page_index INTEGER NOT NULL CHECK(start_page_index >= 0),
                printed_page TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(project_id, ordinal)
            );

            CREATE INDEX IF NOT EXISTS idx_chapters_project_start
                ON chapters(project_id, start_page_index, ordinal);
            """
        )

    @staticmethod
    def _create_v7(connection: sqlite3.Connection) -> None:
        """Record the reading direction an alignment was computed in.

        Direction changes the meaning of every stored pair: the same geometry
        aligned horizontally versus vertically pairs different lines. Without the
        direction on the verification run, a stored alignment cannot be
        interpreted (let alone reproduced) once a page's declared direction or a
        detection heuristic changes.
        """

        connection.executescript(
            """
            ALTER TABLE verification_runs ADD COLUMN direction TEXT;
            """
        )

    @staticmethod
    def _last_id(cursor: sqlite3.Cursor) -> int:
        value = cursor.lastrowid
        if value is None:
            raise RuntimeError("SQLite did not return an inserted row id")
        return int(value)

    def create_project(self, title: str, source_uri: str, source_sha256: str) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "INSERT INTO projects(title, source_uri, source_sha256) VALUES (?, ?, ?)",
                (title, source_uri, source_sha256),
            )
            return self._last_id(cursor)

    def list_projects(self) -> tuple[ProjectRecord, ...]:
        """Return every persisted project in creation order."""

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, title, source_uri, source_sha256, created_at
                FROM projects
                ORDER BY id
                """
            ).fetchall()
            return tuple(
                ProjectRecord(
                    id=int(row["id"]),
                    title=str(row["title"]),
                    source_uri=str(row["source_uri"]),
                    source_sha256=str(row["source_sha256"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            )

    def relink_project_source(
        self, project_id: int, source_uri: str, *, expected_sha256: str
    ) -> None:
        """Change only the source path when the stored content identity still matches."""
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE projects
                SET source_uri = ?
                WHERE id = ? AND source_sha256 = ?
                """,
                (source_uri, project_id, expected_sha256),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    "cannot relink source: project is missing or recorded SHA-256 changed"
                )

    def add_page(
        self,
        project_id: int,
        page_index: int,
        source_image: str,
        width: int,
        height: int,
    ) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pages(project_id, page_index, source_image, width, height)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, page_index, source_image, width, height),
            )
            return self._last_id(cursor)

    def imported_page_indexes(self, project_id: int) -> frozenset[int]:
        """Return the page indexes already materialized for a project."""

        with self._connection() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT page_index FROM pages WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            return frozenset(int(row["page_index"]) for row in rows)

    def list_pages(self, project_id: int) -> tuple[PageRecord, ...]:
        """Return persisted page evidence ordered by page index."""

        with self._connection() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT id, project_id, page_index, source_image, width, height
                FROM pages
                WHERE project_id = ?
                ORDER BY page_index
                """,
                (project_id,),
            ).fetchall()
            return tuple(
                PageRecord(
                    id=int(row["id"]),
                    project_id=int(row["project_id"]),
                    page_index=int(row["page_index"]),
                    source_image=str(row["source_image"]),
                    width=int(row["width"]),
                    height=int(row["height"]),
                )
                for row in rows
            )

    def latest_engine_run(self, project_id: int, page_index: int, engine: str) -> int:
        """Resolve the newest persisted OCR run for one engine on one page."""

        with self._connection() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                """
                SELECT engine_runs.id
                FROM engine_runs
                JOIN pages ON pages.id = engine_runs.page_id
                WHERE pages.project_id = ?
                  AND pages.page_index = ?
                  AND engine_runs.engine = ?
                ORDER BY engine_runs.id DESC
                LIMIT 1
                """,
                (project_id, page_index, engine),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown OCR run for engine {engine} on page {page_index}")
            return int(row["id"])

    def latest_verification_id(self, project_id: int, page_index: int) -> int:
        """Resolve the newest persisted verification run for one page."""

        with self._connection() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                """
                SELECT verification_runs.id
                FROM verification_runs
                JOIN pages ON pages.id = verification_runs.page_id
                WHERE pages.project_id = ? AND pages.page_index = ?
                ORDER BY verification_runs.id DESC
                LIMIT 1
                """,
                (project_id, page_index),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown verification run for page {page_index}")
            return int(row["id"])

    @staticmethod
    def _page_id(connection: sqlite3.Connection, project_id: int, page_index: int) -> int:
        row = connection.execute(
            "SELECT id FROM pages WHERE project_id = ? AND page_index = ?",
            (project_id, page_index),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown project/page: {project_id}/{page_index}")
        return int(row["id"])

    @staticmethod
    def _require_project(connection: sqlite3.Connection, project_id: int) -> None:
        row = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown project: {project_id}")

    @staticmethod
    def _job_record(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            page_index=None if row["page_index"] is None else int(row["page_index"]),
            stage=JobStage(str(row["stage"])),
            state=JobState(str(row["state"])),
            attempts=int(row["attempts"]),
            last_error=None if row["last_error"] is None else str(row["last_error"]),
            created_at=str(row["created_at"]),
            started_at=None if row["started_at"] is None else str(row["started_at"]),
            finished_at=None if row["finished_at"] is None else str(row["finished_at"]),
        )

    @staticmethod
    def _job_row(connection: sqlite3.Connection, job_id: int) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT jobs.id, jobs.project_id, pages.page_index, jobs.stage, jobs.state,
                   jobs.attempts, jobs.last_error, jobs.created_at,
                   jobs.started_at, jobs.finished_at
            FROM jobs
            LEFT JOIN pages ON pages.id = jobs.page_id
            WHERE jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown job: {job_id}")
        return cast(sqlite3.Row, row)

    def ensure_job(
        self,
        project_id: int,
        stage: JobStage,
        *,
        page_index: int | None = None,
    ) -> int:
        if stage.page_scoped and page_index is None:
            raise ValueError(f"{stage.value} is a page-scoped job stage")
        if not stage.page_scoped and page_index is not None:
            raise ValueError(f"{stage.value} is a project-scoped job stage")

        with self._connection() as connection:
            self._require_project(connection, project_id)
            page_id = (
                None
                if page_index is None
                else self._page_id(connection, project_id, page_index)
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO jobs(project_id, page_id, stage)
                VALUES (?, ?, ?)
                """,
                (project_id, page_id, stage.value),
            )
            row = connection.execute(
                """
                SELECT id FROM jobs
                WHERE project_id = ? AND stage = ? AND page_id IS ?
                """,
                (project_id, stage.value, page_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("failed to create or resolve processing job")
            return int(row["id"])

    def load_job(self, job_id: int) -> JobRecord:
        with self._connection() as connection:
            return self._job_record(self._job_row(connection, job_id))

    def start_job(self, job_id: int) -> JobRecord:
        with self._connection() as connection:
            current = self._job_record(self._job_row(connection, job_id))
            if current.state is JobState.SUCCEEDED:
                raise ValueError("succeeded job cannot be started again")
            connection.execute(
                """
                UPDATE jobs
                SET state = 'running', attempts = attempts + 1, last_error = NULL,
                    started_at = CURRENT_TIMESTAMP, finished_at = NULL
                WHERE id = ?
                """,
                (job_id,),
            )
            return self._job_record(self._job_row(connection, job_id))

    def complete_job(self, job_id: int) -> JobRecord:
        with self._connection() as connection:
            current = self._job_record(self._job_row(connection, job_id))
            if current.state is not JobState.RUNNING:
                raise ValueError("job must be running before it can succeed")
            connection.execute(
                """
                UPDATE jobs
                SET state = 'succeeded', finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
            return self._job_record(self._job_row(connection, job_id))

    def reopen_job(self, job_id: int, reason: str) -> JobRecord:
        """Return a succeeded job to pending so its stage can run again.

        Needed when the job's own record says a stage succeeded but the evidence
        it should have produced is missing or was overwritten — a restored
        backup, a deleted asset directory, or settings the operator deliberately
        changed. The reason is preserved so the re-run is explainable.
        """

        reason = reason.strip()
        if not reason:
            raise ValueError("reopen reason must not be blank")
        with self._connection() as connection:
            current = self._job_record(self._job_row(connection, job_id))
            if current.state is not JobState.SUCCEEDED:
                raise ValueError("only a succeeded job can be reopened")
            connection.execute(
                """
                UPDATE jobs
                SET state = 'pending', last_error = ?, finished_at = NULL
                WHERE id = ?
                """,
                (reason, job_id),
            )
            return self._job_record(self._job_row(connection, job_id))

    def fail_job(self, job_id: int, error: str) -> JobRecord:
        error = error.strip()
        if not error:
            raise ValueError("job failure error must not be blank")
        with self._connection() as connection:
            current = self._job_record(self._job_row(connection, job_id))
            if current.state is not JobState.RUNNING:
                raise ValueError("job must be running before it can fail")
            connection.execute(
                """
                UPDATE jobs
                SET state = 'failed', last_error = ?, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error, job_id),
            )
            return self._job_record(self._job_row(connection, job_id))

    def list_resumable_jobs(self, project_id: int) -> tuple[JobRecord, ...]:
        with self._connection() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT jobs.id, jobs.project_id, pages.page_index, jobs.stage, jobs.state,
                       jobs.attempts, jobs.last_error, jobs.created_at,
                       jobs.started_at, jobs.finished_at
                FROM jobs
                LEFT JOIN pages ON pages.id = jobs.page_id
                WHERE jobs.project_id = ?
                  AND jobs.state IN ('pending', 'running', 'failed')
                ORDER BY jobs.id
                """,
                (project_id,),
            ).fetchall()
            return tuple(self._job_record(row) for row in rows)

    def list_jobs(self, project_id: int) -> tuple[JobRecord, ...]:
        """Return every processing job for a project, in creation order."""

        with self._connection() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT jobs.id, jobs.project_id, pages.page_index, jobs.stage, jobs.state,
                       jobs.attempts, jobs.last_error, jobs.created_at,
                       jobs.started_at, jobs.finished_at
                FROM jobs
                LEFT JOIN pages ON pages.id = jobs.page_id
                WHERE jobs.project_id = ?
                ORDER BY jobs.id
                """,
                (project_id,),
            ).fetchall()
            return tuple(self._job_record(row) for row in rows)

    def record_preprocess(
        self,
        project_id: int,
        page_index: int,
        *,
        settings: Mapping[str, object],
        applied: tuple[str, ...],
        measured_skew_degrees: float,
        output_image: str,
        output_sha256: str,
        output_width: int,
        output_height: int,
        notes: Mapping[str, object],
    ) -> int:
        """Persist the preprocessing evidence for one page, replacing any prior run.

        Re-running with different settings must overwrite rather than accumulate:
        only the raster the OCR engines actually consumed is meaningful, and two
        conflicting rows would leave no way to tell which one that was.
        """

        if output_width <= 0 or output_height <= 0:
            raise ValueError("preprocessed page dimensions must be positive")
        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, page_index)
            connection.execute(
                """
                INSERT INTO page_preprocess(
                    page_id, settings_json, applied_json, measured_skew_degrees,
                    output_image, output_sha256, output_width, output_height,
                    notes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    applied_json = excluded.applied_json,
                    measured_skew_degrees = excluded.measured_skew_degrees,
                    output_image = excluded.output_image,
                    output_sha256 = excluded.output_sha256,
                    output_width = excluded.output_width,
                    output_height = excluded.output_height,
                    notes_json = excluded.notes_json,
                    created_at = CURRENT_TIMESTAMP
                """,
                (
                    page_id,
                    json.dumps(dict(settings), sort_keys=True),
                    json.dumps(list(applied)),
                    float(measured_skew_degrees),
                    output_image,
                    output_sha256,
                    int(output_width),
                    int(output_height),
                    json.dumps(dict(notes), sort_keys=True),
                ),
            )
            return page_id

    def load_preprocess(
        self, project_id: int, page_index: int
    ) -> PreprocessRecord | None:
        """Return the stored preprocessing evidence, or ``None`` if absent."""

        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, page_index)
            row = connection.execute(
                """
                SELECT settings_json, applied_json, measured_skew_degrees,
                       output_image, output_sha256, output_width, output_height,
                       notes_json, created_at
                FROM page_preprocess
                WHERE page_id = ?
                """,
                (page_id,),
            ).fetchone()
            if row is None:
                return None
            return PreprocessRecord(
                page_index=page_index,
                settings=json.loads(str(row["settings_json"])),
                applied=tuple(json.loads(str(row["applied_json"]))),
                measured_skew_degrees=float(row["measured_skew_degrees"]),
                output_image=str(row["output_image"]),
                output_sha256=str(row["output_sha256"]),
                output_width=int(row["output_width"]),
                output_height=int(row["output_height"]),
                notes=json.loads(str(row["notes_json"])),
                created_at=str(row["created_at"]),
            )

    def record_page_structure(
        self,
        project_id: int,
        structures: tuple[PageStructure, ...],
    ) -> None:
        """Replace the stored page roles and printed labels for a project.

        A structure pass is a whole-book decision (numbering sequences only make
        sense across pages), so it replaces rather than accumulates: partial rows
        from an earlier heuristic would contradict the new ones with no way to
        tell which is authoritative.
        """

        with self._connection() as connection:
            self._require_project(connection, project_id)
            page_ids = {
                int(row["page_index"]): int(row["id"])
                for row in connection.execute(
                    "SELECT id, page_index FROM pages WHERE project_id = ?",
                    (project_id,),
                )
            }
            for structure in structures:
                page_id = page_ids.get(structure.page_index)
                if page_id is None:
                    raise KeyError(f"unknown page index: {structure.page_index}")
                connection.execute(
                    """
                    INSERT INTO page_structure(
                        page_id, role, printed_label, numbering_scheme,
                        sequence_number, sequence_ordinal
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(page_id) DO UPDATE SET
                        role = excluded.role,
                        printed_label = excluded.printed_label,
                        numbering_scheme = excluded.numbering_scheme,
                        sequence_number = excluded.sequence_number,
                        sequence_ordinal = excluded.sequence_ordinal,
                        created_at = CURRENT_TIMESTAMP
                    """,
                    (
                        page_id,
                        structure.role.value,
                        structure.label.printed_label,
                        structure.label.scheme.value,
                        structure.label.sequence,
                        structure.label.sequence_ordinal,
                    ),
                )

    def record_chapters(self, project_id: int, chapters: tuple[Chapter, ...]) -> None:
        """Replace the chapter tree for a project."""

        with self._connection() as connection:
            self._require_project(connection, project_id)
            connection.execute("DELETE FROM chapters WHERE project_id = ?", (project_id,))
            for chapter in chapters:
                connection.execute(
                    """
                    INSERT INTO chapters(
                        project_id, ordinal, level, title, start_page_index, printed_page
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        chapter.ordinal,
                        chapter.level,
                        chapter.title,
                        chapter.start_page_index,
                        chapter.printed_page,
                    ),
                )

    def load_book_structure(self, project_id: int) -> BookStructure:
        """Return the stored structure, with defaults for pages never classified."""

        with self._connection() as connection:
            self._require_project(connection, project_id)
            page_rows = connection.execute(
                """
                SELECT pages.page_index AS page_index,
                       page_structure.role AS role,
                       page_structure.printed_label AS printed_label,
                       page_structure.numbering_scheme AS numbering_scheme,
                       page_structure.sequence_number AS sequence_number,
                       page_structure.sequence_ordinal AS sequence_ordinal
                FROM pages
                LEFT JOIN page_structure ON page_structure.page_id = pages.id
                WHERE pages.project_id = ?
                ORDER BY pages.page_index
                """,
                (project_id,),
            ).fetchall()
            chapter_rows = connection.execute(
                """
                SELECT ordinal, level, title, start_page_index, printed_page
                FROM chapters
                WHERE project_id = ?
                ORDER BY start_page_index, ordinal
                """,
                (project_id,),
            ).fetchall()

        pages: list[PageStructure] = []
        for row in page_rows:
            scheme = (
                NumberingScheme.NONE
                if row["numbering_scheme"] is None
                else NumberingScheme(str(row["numbering_scheme"]))
            )
            label = PageLabel(
                printed_label=str(row["printed_label"] or ""),
                scheme=scheme,
                sequence=(
                    None if row["sequence_number"] is None else int(row["sequence_number"])
                ),
                sequence_ordinal=(
                    None if row["sequence_ordinal"] is None else int(row["sequence_ordinal"])
                ),
            )
            pages.append(
                PageStructure(
                    page_index=int(row["page_index"]),
                    role=(
                        PageRole.UNKNOWN
                        if row["role"] is None
                        else PageRole(str(row["role"]))
                    ),
                    label=label,
                )
            )

        chapters = tuple(
            Chapter(
                ordinal=int(row["ordinal"]),
                level=int(row["level"]),
                title=str(row["title"]),
                start_page_index=int(row["start_page_index"]),
                printed_page=str(row["printed_page"]),
            )
            for row in chapter_rows
        )
        return BookStructure(tuple(pages), chapters)

    def record_ocr(self, project_id: int, result: PageOCRResult) -> int:
        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, result.page_index)
            cursor = connection.execute(
                """
                INSERT INTO engine_runs(page_id, engine, engine_version, direction)
                VALUES (?, ?, ?, ?)
                """,
                (
                    page_id,
                    result.engine,
                    result.engine_version,
                    result.direction.value if result.direction_declared else None,
                ),
            )
            run_id = self._last_id(cursor)
            for line_index, line in enumerate(result.lines):
                line_cursor = connection.execute(
                    """
                    INSERT INTO ocr_lines(
                        run_id, line_index, text, x, y, width, height, confidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        line_index,
                        line.text,
                        line.box.x,
                        line.box.y,
                        line.box.width,
                        line.box.height,
                        line.confidence,
                    ),
                )
                line_id = self._last_id(line_cursor)
                for token_index, token in enumerate(line.tokens):
                    connection.execute(
                        """
                        INSERT INTO ocr_tokens(
                            line_id, token_index, text, confidence, x, y, width, height
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            line_id,
                            token_index,
                            token.text,
                            token.confidence,
                            token.box.x,
                            token.box.y,
                            token.box.width,
                            token.box.height,
                        ),
                    )
            for block_index, block in enumerate(result.blocks):
                connection.execute(
                    """
                    INSERT INTO ocr_blocks(
                        run_id, block_index, block_type, x, y, width, height,
                        line_start, line_end
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        block_index,
                        block.block_type.value,
                        block.box.x,
                        block.box.y,
                        block.box.width,
                        block.box.height,
                        block.line_start,
                        block.line_end,
                    ),
                )
            return run_id

    def load_ocr_run(self, run_id: int) -> PageOCRResult:
        with self._connection() as connection:
            run = connection.execute(
                """
                SELECT engine_runs.engine, engine_runs.engine_version,
                       engine_runs.direction, pages.page_index
                FROM engine_runs
                JOIN pages ON pages.id = engine_runs.page_id
                WHERE engine_runs.id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown OCR run: {run_id}")

            line_rows = connection.execute(
                """
                SELECT id, line_index, text, x, y, width, height, confidence
                FROM ocr_lines
                WHERE run_id = ?
                ORDER BY line_index
                """,
                (run_id,),
            ).fetchall()
            lines: list[OCRLine] = []
            for line_row in line_rows:
                token_rows = connection.execute(
                    """
                    SELECT text, confidence, x, y, width, height
                    FROM ocr_tokens
                    WHERE line_id = ?
                    ORDER BY token_index
                    """,
                    (int(line_row["id"]),),
                ).fetchall()
                tokens = tuple(
                    OCRToken(
                        text=str(token_row["text"]),
                        confidence=(
                            None
                            if token_row["confidence"] is None
                            else float(token_row["confidence"])
                        ),
                        box=BoundingBox(
                            float(token_row["x"]),
                            float(token_row["y"]),
                            float(token_row["width"]),
                            float(token_row["height"]),
                        ),
                    )
                    for token_row in token_rows
                )
                lines.append(
                    OCRLine(
                        text=str(line_row["text"]),
                        box=BoundingBox(
                            float(line_row["x"]),
                            float(line_row["y"]),
                            float(line_row["width"]),
                            float(line_row["height"]),
                        ),
                        tokens=tokens,
                        confidence=(
                            None
                            if line_row["confidence"] is None
                            else float(line_row["confidence"])
                        ),
                    )
                )

            block_rows = connection.execute(
                """
                SELECT block_type, x, y, width, height, line_start, line_end
                FROM ocr_blocks
                WHERE run_id = ?
                ORDER BY block_index
                """,
                (run_id,),
            ).fetchall()
            blocks = tuple(
                OCRBlock(
                    block_type=BlockType(str(block_row["block_type"])),
                    box=BoundingBox(
                        float(block_row["x"]),
                        float(block_row["y"]),
                        float(block_row["width"]),
                        float(block_row["height"]),
                    ),
                    line_start=int(block_row["line_start"]),
                    line_end=int(block_row["line_end"]),
                )
                for block_row in block_rows
            )

            direction = run["direction"]
            return PageOCRResult(
                page_index=int(run["page_index"]),
                engine=str(run["engine"]),
                engine_version=str(run["engine_version"]),
                lines=tuple(lines),
                blocks=blocks,
                direction=(
                    PageDirection.HORIZONTAL_LR
                    if direction is None
                    else PageDirection(str(direction))
                ),
                direction_declared=direction is not None,
            )

    @staticmethod
    def _engine_evidence(
        connection: sqlite3.Connection, run_id: int
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT engine_runs.id, engine_runs.page_id, engine_runs.engine,
                   pages.page_index
            FROM engine_runs
            JOIN pages ON pages.id = engine_runs.page_id
            WHERE engine_runs.id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown OCR run: {run_id}")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _validate_verification(
        primary: sqlite3.Row,
        secondary: sqlite3.Row,
        alignment: PageAlignment,
        risks: tuple[PairRisk, ...],
        candidates: tuple[ReviewCandidate, ...],
    ) -> None:
        if primary["page_id"] != secondary["page_id"]:
            raise ValueError("verification engine runs must describe the same page")
        if int(primary["page_index"]) != alignment.page_index:
            raise ValueError("alignment must describe the same page as OCR evidence")
        if str(primary["engine"]) != alignment.primary_engine:
            raise ValueError("primary engine identity does not match alignment")
        if str(secondary["engine"]) != alignment.secondary_engine:
            raise ValueError("secondary engine identity does not match alignment")
        if len(risks) != len(alignment.pairs):
            raise ValueError("risk assessments must match alignment pairs")
        if any(risk.pair_index != index for index, risk in enumerate(risks)):
            raise ValueError("risk pair indexes must match alignment order")
        for candidate in candidates:
            if candidate.page_index != alignment.page_index:
                raise ValueError("review candidate page does not match alignment")
            if not 0 <= candidate.pair_index < len(alignment.pairs):
                raise ValueError("review candidate pair index is out of range")

    def record_verification(
        self,
        primary_run_id: int,
        secondary_run_id: int,
        alignment: PageAlignment,
        risks: tuple[PairRisk, ...],
        candidates: tuple[ReviewCandidate, ...],
    ) -> int:
        with self._connection() as connection:
            primary = self._engine_evidence(connection, primary_run_id)
            secondary = self._engine_evidence(connection, secondary_run_id)
            self._validate_verification(primary, secondary, alignment, risks, candidates)
            cursor = connection.execute(
                """
                INSERT INTO verification_runs(
                    page_id, primary_run_id, secondary_run_id, direction
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    int(primary["page_id"]),
                    primary_run_id,
                    secondary_run_id,
                    alignment.direction.value,
                ),
            )
            verification_id = self._last_id(cursor)
            alignment_ids: list[int] = []
            for pair_index, pair in enumerate(alignment.pairs):
                pair_cursor = connection.execute(
                    """
                    INSERT INTO alignments(
                        verification_run_id, pair_index, primary_line_index,
                        secondary_line_index, text_similarity, vertical_distance
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        verification_id,
                        pair_index,
                        pair.primary_line_index,
                        pair.secondary_line_index,
                        pair.text_similarity,
                        pair.cross_axis_distance,
                    ),
                )
                alignment_id = self._last_id(pair_cursor)
                alignment_ids.append(alignment_id)
                for conflict_index, conflict in enumerate(pair.conflicts):
                    connection.execute(
                        """
                        INSERT INTO alignment_conflicts(
                            alignment_id, conflict_index, kind, primary_text,
                            secondary_text, primary_start, primary_end,
                            secondary_start, secondary_end
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            alignment_id,
                            conflict_index,
                            conflict.kind.value,
                            conflict.primary_text,
                            conflict.secondary_text,
                            conflict.primary_start,
                            conflict.primary_end,
                            conflict.secondary_start,
                            conflict.secondary_end,
                        ),
                    )
                risk = risks[pair_index]
                connection.execute(
                    "INSERT INTO risks(alignment_id, score, reasons_json) VALUES (?, ?, ?)",
                    (
                        alignment_id,
                        risk.score,
                        json.dumps([reason.value for reason in risk.reasons]),
                    ),
                )

            for candidate in candidates:
                connection.execute(
                    """
                    INSERT INTO review_candidates(
                        alignment_id, score, reasons_json, crop_x, crop_y,
                        crop_width, crop_height
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alignment_ids[candidate.pair_index],
                        candidate.score,
                        json.dumps([reason.value for reason in candidate.reasons]),
                        candidate.crop.x,
                        candidate.crop.y,
                        candidate.crop.width,
                        candidate.crop.height,
                    ),
                )
            return verification_id

    @staticmethod
    def _json_strings(value: Any) -> tuple[str, ...]:
        decoded = json.loads(str(value))
        if not isinstance(decoded, list):
            raise TypeError("stored JSON list is invalid")
        return tuple(str(item) for item in decoded)

    def load_verification(self, verification_id: int) -> VerificationRecord:
        with self._connection() as connection:
            run = connection.execute(
                """
                SELECT vr.primary_run_id, vr.secondary_run_id, pages.page_index,
                       vr.direction AS direction,
                       primary_run.engine AS primary_engine,
                       secondary_run.engine AS secondary_engine
                FROM verification_runs AS vr
                JOIN pages ON pages.id = vr.page_id
                JOIN engine_runs AS primary_run ON primary_run.id = vr.primary_run_id
                JOIN engine_runs AS secondary_run ON secondary_run.id = vr.secondary_run_id
                WHERE vr.id = ?
                """,
                (verification_id,),
            ).fetchone()
            if run is None:
                raise KeyError(f"unknown verification run: {verification_id}")

            rows = connection.execute(
                """
                SELECT id, pair_index, primary_line_index, secondary_line_index,
                       text_similarity, vertical_distance
                FROM alignments
                WHERE verification_run_id = ?
                ORDER BY pair_index
                """,
                (verification_id,),
            ).fetchall()
            pairs: list[LineAlignment] = []
            risks: list[PairRisk] = []
            alignment_ids: dict[int, int] = {}
            for row in rows:
                pair_index = int(row["pair_index"])
                alignment_id = int(row["id"])
                alignment_ids[alignment_id] = pair_index
                conflict_rows = connection.execute(
                    """
                    SELECT kind, primary_text, secondary_text, primary_start,
                           primary_end, secondary_start, secondary_end
                    FROM alignment_conflicts
                    WHERE alignment_id = ?
                    ORDER BY conflict_index
                    """,
                    (alignment_id,),
                ).fetchall()
                conflicts = tuple(
                    AlignmentConflict(
                        kind=ConflictKind(str(conflict["kind"])),
                        primary_text=str(conflict["primary_text"]),
                        secondary_text=str(conflict["secondary_text"]),
                        primary_start=int(conflict["primary_start"]),
                        primary_end=int(conflict["primary_end"]),
                        secondary_start=int(conflict["secondary_start"]),
                        secondary_end=int(conflict["secondary_end"]),
                    )
                    for conflict in conflict_rows
                )
                pairs.append(
                    LineAlignment(
                        primary_line_index=(
                            None
                            if row["primary_line_index"] is None
                            else int(row["primary_line_index"])
                        ),
                        secondary_line_index=(
                            None
                            if row["secondary_line_index"] is None
                            else int(row["secondary_line_index"])
                        ),
                        text_similarity=float(row["text_similarity"]),
                        cross_axis_distance=(
                            None
                            if row["vertical_distance"] is None
                            else float(row["vertical_distance"])
                        ),
                        conflicts=conflicts,
                    )
                )
                risk_row = connection.execute(
                    "SELECT score, reasons_json FROM risks WHERE alignment_id = ?",
                    (alignment_id,),
                ).fetchone()
                if risk_row is None:
                    raise ValueError("stored alignment is missing its risk assessment")
                risks.append(
                    PairRisk(
                        pair_index=pair_index,
                        score=float(risk_row["score"]),
                        reasons=tuple(
                            RiskReason(value)
                            for value in self._json_strings(risk_row["reasons_json"])
                        ),
                    )
                )

            candidate_rows = connection.execute(
                """
                SELECT alignment_id, score, reasons_json, crop_x, crop_y,
                       crop_width, crop_height
                FROM review_candidates
                WHERE alignment_id IN (
                    SELECT id FROM alignments WHERE verification_run_id = ?
                )
                ORDER BY id
                """,
                (verification_id,),
            ).fetchall()
            candidates = tuple(
                ReviewCandidate(
                    page_index=int(run["page_index"]),
                    pair_index=alignment_ids[int(row["alignment_id"])],
                    score=float(row["score"]),
                    reasons=tuple(
                        RiskReason(value)
                        for value in self._json_strings(row["reasons_json"])
                    ),
                    crop=BoundingBox(
                        float(row["crop_x"]),
                        float(row["crop_y"]),
                        float(row["crop_width"]),
                        float(row["crop_height"]),
                    ),
                )
                for row in candidate_rows
            )
            stored_direction = run["direction"]
            alignment = PageAlignment(
                page_index=int(run["page_index"]),
                primary_engine=str(run["primary_engine"]),
                secondary_engine=str(run["secondary_engine"]),
                pairs=tuple(pairs),
                direction=(
                    PageDirection.HORIZONTAL_LR
                    if stored_direction is None
                    else PageDirection(str(stored_direction))
                ),
            )
            return VerificationRecord(
                id=verification_id,
                primary_run_id=int(run["primary_run_id"]),
                secondary_run_id=int(run["secondary_run_id"]),
                alignment=alignment,
                risks=tuple(risks),
                candidates=candidates,
            )

    def verification_page_index(self, verification_id: int) -> int:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT pages.page_index
                FROM verification_runs
                JOIN pages ON pages.id = verification_runs.page_id
                WHERE verification_runs.id = ?
                """,
                (verification_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown verification run: {verification_id}")
            return int(row["page_index"])

    @staticmethod
    def _review_candidate_for_reading(
        connection: sqlite3.Connection,
        verification_id: int,
        reading: VLMReading,
    ) -> sqlite3.Row:
        rows = connection.execute(
            """
            SELECT review_candidates.id AS candidate_id, pages.page_index
            FROM review_candidates
            JOIN alignments ON alignments.id = review_candidates.alignment_id
            JOIN verification_runs ON verification_runs.id = alignments.verification_run_id
            JOIN pages ON pages.id = verification_runs.page_id
            WHERE verification_runs.id = ? AND alignments.pair_index = ?
            ORDER BY review_candidates.id
            """,
            (verification_id, reading.pair_index),
        ).fetchall()
        if not rows:
            raise ValueError("VLM review requires a matching persisted review candidate")
        if len(rows) != 1:
            raise RuntimeError("stored verification has duplicate review candidates for one pair")
        row = cast(sqlite3.Row, rows[0])
        if int(row["page_index"]) != reading.page_index:
            raise ValueError("VLM reading page does not match verification candidate page")
        return row

    def record_vlm_review(
        self,
        verification_id: int,
        reading: VLMReading,
        *,
        crop_path: str,
        crop_sha256: str,
    ) -> int:
        if not crop_path.strip():
            raise ValueError("VLM review crop path is required")
        if not crop_sha256.strip():
            raise ValueError("VLM review crop SHA-256 is required")
        with self._connection() as connection:
            candidate = self._review_candidate_for_reading(connection, verification_id, reading)
            cursor = connection.execute(
                """
                INSERT INTO vlm_reviews(
                    review_candidate_id, model, model_version, text, uncertain,
                    crop_path, crop_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(candidate["candidate_id"]),
                    reading.model,
                    reading.model_version,
                    reading.text,
                    int(reading.uncertain),
                    crop_path,
                    crop_sha256,
                ),
            )
            return self._last_id(cursor)

    def list_vlm_reviews(self, verification_id: int) -> tuple[VLMReviewRecord, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT vlm_reviews.id, alignments.pair_index, pages.page_index,
                       vlm_reviews.model, vlm_reviews.model_version, vlm_reviews.text,
                       vlm_reviews.uncertain, vlm_reviews.crop_path,
                       vlm_reviews.crop_sha256, vlm_reviews.created_at
                FROM vlm_reviews
                JOIN review_candidates
                    ON review_candidates.id = vlm_reviews.review_candidate_id
                JOIN alignments ON alignments.id = review_candidates.alignment_id
                JOIN verification_runs
                    ON verification_runs.id = alignments.verification_run_id
                JOIN pages ON pages.id = verification_runs.page_id
                WHERE verification_runs.id = ?
                ORDER BY vlm_reviews.id
                """,
                (verification_id,),
            ).fetchall()
            return tuple(
                VLMReviewRecord(
                    id=int(row["id"]),
                    verification_run_id=verification_id,
                    pair_index=int(row["pair_index"]),
                    crop_path=str(row["crop_path"]),
                    crop_sha256=str(row["crop_sha256"]),
                    reading=VLMReading(
                        page_index=int(row["page_index"]),
                        pair_index=int(row["pair_index"]),
                        model=str(row["model"]),
                        model_version=str(row["model_version"]),
                        text=str(row["text"]),
                        uncertain=bool(int(row["uncertain"])),
                    ),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            )

    def reviewed_pair_indexes(self, verification_id: int) -> frozenset[int]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT alignments.pair_index
                FROM vlm_reviews
                JOIN review_candidates
                    ON review_candidates.id = vlm_reviews.review_candidate_id
                JOIN alignments ON alignments.id = review_candidates.alignment_id
                WHERE alignments.verification_run_id = ?
                """,
                (verification_id,),
            ).fetchall()
            return frozenset(int(row["pair_index"]) for row in rows)

    def append_decision(
        self,
        *,
        project_id: int,
        page_index: int,
        pair_index: int,
        selected_text: str,
        actor: str,
        evidence_refs: tuple[str, ...],
    ) -> int:
        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, page_index)
            cursor = connection.execute(
                """
                INSERT INTO decisions(
                    page_id, pair_index, selected_text, actor, evidence_refs_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    page_id,
                    pair_index,
                    selected_text,
                    actor,
                    json.dumps(evidence_refs, ensure_ascii=False),
                ),
            )
            return self._last_id(cursor)

    def list_decisions(
        self, project_id: int, page_index: int, pair_index: int
    ) -> tuple[DecisionRecord, ...]:
        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, page_index)
            rows = connection.execute(
                """
                SELECT id, selected_text, actor, evidence_refs_json, created_at
                FROM decisions
                WHERE page_id = ? AND pair_index = ?
                ORDER BY id
                """,
                (page_id, pair_index),
            ).fetchall()
            return tuple(
                DecisionRecord(
                    id=int(row["id"]),
                    project_id=project_id,
                    page_index=page_index,
                    pair_index=pair_index,
                    selected_text=str(row["selected_text"]),
                    actor=str(row["actor"]),
                    evidence_refs=self._json_strings(row["evidence_refs_json"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            )

    def latest_decision(
        self, project_id: int, page_index: int, pair_index: int
    ) -> DecisionRecord | None:
        """Return the newest accepted decision for a page pair, if any."""

        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, page_index)
            row = connection.execute(
                """
                SELECT id, selected_text, actor, evidence_refs_json, created_at
                FROM decisions
                WHERE page_id = ? AND pair_index = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (page_id, pair_index),
            ).fetchone()
            if row is None:
                return None
            return DecisionRecord(
                id=int(row["id"]),
                project_id=project_id,
                page_index=page_index,
                pair_index=pair_index,
                selected_text=str(row["selected_text"]),
                actor=str(row["actor"]),
                evidence_refs=self._json_strings(row["evidence_refs_json"]),
                created_at=str(row["created_at"]),
            )