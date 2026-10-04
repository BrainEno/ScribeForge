from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
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
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import PairRisk, ReviewCandidate, RiskReason
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
    SCHEMA_VERSION = 4

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

    def record_ocr(self, project_id: int, result: PageOCRResult) -> int:
        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, result.page_index)
            cursor = connection.execute(
                """
                INSERT INTO engine_runs(page_id, engine, engine_version)
                VALUES (?, ?, ?)
                """,
                (page_id, result.engine, result.engine_version),
            )
            run_id = self._last_id(cursor)
            for line_index, line in enumerate(result.lines):
                line_cursor = connection.execute(
                    """
                    INSERT INTO ocr_lines(
                        run_id, line_index, text, x, y, width, height
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        line_index,
                        line.text,
                        line.box.x,
                        line.box.y,
                        line.box.width,
                        line.box.height,
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
            return run_id

    def load_ocr_run(self, run_id: int) -> PageOCRResult:
        with self._connection() as connection:
            run = connection.execute(
                """
                SELECT engine_runs.engine, engine_runs.engine_version, pages.page_index
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
                SELECT id, line_index, text, x, y, width, height
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
                    )
                )

            return PageOCRResult(
                page_index=int(run["page_index"]),
                engine=str(run["engine"]),
                engine_version=str(run["engine_version"]),
                lines=tuple(lines),
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
                INSERT INTO verification_runs(page_id, primary_run_id, secondary_run_id)
                VALUES (?, ?, ?)
                """,
                (int(primary["page_id"]), primary_run_id, secondary_run_id),
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
                        pair.vertical_distance,
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
                        vertical_distance=(
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
            alignment = PageAlignment(
                page_index=int(run["page_index"]),
                primary_engine=str(run["primary_engine"]),
                secondary_engine=str(run["secondary_engine"]),
                pairs=tuple(pairs),
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