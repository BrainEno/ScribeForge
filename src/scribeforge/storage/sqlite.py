from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

from scribeforge.decision_bindings import inspect_latest_decisions
from scribeforge.domain.alignment import (
    AlignmentAlternative,
    AlignmentAmbiguity,
    AlignmentConflict,
    AlignmentStatus,
    AmbiguityReason,
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
from scribeforge.domain.page_asset import (
    PAGE_ASSET_VERSION,
    PageAssetError,
    PageAssetEvidence,
    parse_page_asset_evidence,
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


def _measure_page_asset(path: Path) -> tuple[str, int]:
    """Hash and count the same byte stream, even if the path is replaced."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


class JobAlreadyRunningError(RuntimeError):
    """A process cannot claim work while another process owns the project lock."""


class DecisionAlreadyExists(RuntimeError):
    """A machine decision lost a race to an existing decision."""


class JobOwnershipError(RuntimeError):
    """Only the store that claimed a job may complete or fail that job."""


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
    #: Risk the page reported for this pair when the decision was accepted, and the
    #: reason codes behind it. ``None`` for rows written before schema v9 and for
    #: pairs whose page reported no risk at all. Stored rather than recomputed so an
    #: audit years later sees the numbers the decision was actually made on, even
    #: after the scoring heuristics change.
    risk_score: float | None = None
    risk_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PageConfirmation:
    """A deliberate statement that a page has no text to transcribe.

    Both engines returning nothing is *not* the same fact as a blank leaf: it is
    also what a crashed worker, a mis-decoded raster or a page torn out of the scan
    look like. Only an explicit confirmation — by a person, or by a measurement
    that proves there is no ink — lets such a page into the export (issue #74).
    """

    id: int
    page_index: int
    kind: str
    actor: str
    reason: str
    ink_ratio: float | None
    created_at: str

    def __post_init__(self) -> None:
        if self.kind not in ("blank", "non_text"):
            raise ValueError("page confirmation kind must be 'blank' or 'non_text'")
        if self.actor not in ("human", "evidence"):
            raise ValueError("page confirmation actor must be 'human' or 'evidence'")
        if not self.reason.strip():
            raise ValueError("a page confirmation must record why it was made")
        if self.ink_ratio is not None and not 0.0 <= self.ink_ratio <= 1.0:
            raise ValueError("ink ratio must be between 0 and 1")


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
    #: Evidence about the rendered page image (schema v11+). Nullable so projects
    #: imported before v11 keep opening; all four NULLs mean "evidence missing".
    page_asset_sha256: str | None = None
    page_asset_byte_size: int | None = None
    page_asset_parameters_json: str | None = None
    page_asset_version: int | None = None

    def asset_evidence(self) -> PageAssetEvidence | None:
        return parse_page_asset_evidence(
            self.page_asset_sha256,
            self.page_asset_byte_size,
            self.page_asset_parameters_json,
            self.page_asset_version,
        )


def _page_record(row: sqlite3.Row) -> PageRecord:
    return PageRecord(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        page_index=int(row["page_index"]),
        source_image=str(row["source_image"]),
        width=int(row["width"]),
        height=int(row["height"]),
        page_asset_sha256=row["page_asset_sha256"],
        page_asset_byte_size=row["page_asset_byte_size"],
        page_asset_parameters_json=row["page_asset_parameters_json"],
        page_asset_version=row["page_asset_version"],
    )


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


@dataclass(frozen=True, slots=True)
class OCRInputFrame:
    kind: str
    sha256: str
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class CropInputEvidence:
    verification_id: int
    pair_index: int
    frame_sha256: str
    crop_sha256: str
    crop_path: str
    x: int
    y: int
    width: int
    height: int
    scale: int


class SQLiteStore:
    SCHEMA_VERSION = 15

    def __init__(self, path: Path) -> None:
        self._path = path
        self._ownership_guard = threading.RLock()
        # Writer locks are keyed by (project_id, page_index): page jobs run under
        # a page-granular lock (issue #70), while book-level stages (page_index
        # None) keep the project-wide lock. Both use the same OS lock file
        # namespace, so cross-process ownership still conflicts.
        self._writer_locks: dict[tuple[int, int | None], BinaryIO] = {}
        self._active_job_ids: dict[tuple[int, int | None], int] = {}

    def _writer_lock_path(self, project_id: int, page_index: int | None) -> Path:
        """Return a stable OS-lock path for one lock scope, outside the assets.

        The lock file is intentionally persistent. Unlinking a lock file while
        another process has it open can give a third process a different inode
        and accidentally create two simultaneous owners.
        """

        database_identity = os.fsencode(os.path.normcase(str(self._path.resolve())))
        database_key = hashlib.sha256(database_identity).hexdigest()
        if page_index is None:
            name = f"project-{project_id}.writer.lock"
        else:
            name = f"project-{project_id}-page-{page_index}.writer.lock"
        return Path(tempfile.gettempdir()) / "scribeforge" / "job-locks" / database_key / name

    def _acquire_project_writer_lock(
        self, project_id: int, page_index: int | None = None
    ) -> BinaryIO:
        lock_path = self._writer_lock_path(project_id, page_index)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                windows_locking = cast(Any, msvcrt)
                windows_locking.locking(handle.fileno(), windows_locking.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise JobAlreadyRunningError(
                    "another ScribeForge process owns the project writer lock"
                ) from exc
            raise
        return handle

    @contextmanager
    def project_writer_guard(self, project_id: int) -> Iterator[None]:
        """Exclude cooperating job writers while a maintenance operation runs."""
        handle = self._acquire_project_writer_lock(project_id)
        try:
            yield
        finally:
            # Closing the handle releases the platform's advisory lock.
            handle.close()

    def _release_project_writer_lock(self, project_id: int, page_index: int | None = None) -> None:
        key = (project_id, page_index)
        handle = self._writer_locks.pop(key, None)
        self._active_job_ids.pop(key, None)
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                windows_locking = cast(Any, msvcrt)
                windows_locking.locking(handle.fileno(), windows_locking.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            # Closing the handle also releases an OS lock. Do not hide a job's
            # already-committed terminal state behind an unlock cleanup failure.
            pass
        finally:
            handle.close()

    def close(self) -> None:
        """Release this store's writer ownership without changing job evidence.

        A job left in ``running`` is recoverable after the handle closes: the
        next process to acquire the OS lock can safely record another attempt.
        """

        with self._ownership_guard:
            for project_id, page_index in tuple(self._writer_locks):
                self._release_project_writer_lock(project_id, page_index)

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
            if version < 8:
                self._create_v8(connection)
                connection.execute("PRAGMA user_version = 8")
            if version < 9:
                self._create_v9(connection)
                connection.execute("PRAGMA user_version = 9")
            if version < 10:
                self._create_v10(connection)
                connection.execute("PRAGMA user_version = 10")
            if version < 11:
                self._create_v11(connection)
                connection.execute("PRAGMA user_version = 11")
            if version < 12:
                self._create_v12(connection)
                connection.execute("PRAGMA user_version = 12")
            if version < 13:
                connection.execute("""CREATE TABLE IF NOT EXISTS ocr_input_frames (
                    run_id INTEGER PRIMARY KEY REFERENCES engine_runs(id) ON DELETE RESTRICT,
                    kind TEXT NOT NULL CHECK(kind IN ('original', 'preprocessed')),
                    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
                    width INTEGER NOT NULL CHECK(width > 0),
                    height INTEGER NOT NULL CHECK(height > 0)
                )""")
                connection.execute("PRAGMA user_version = 13")
            if version < 14:
                connection.execute("""CREATE TABLE IF NOT EXISTS crop_input_evidence (
                    id INTEGER PRIMARY KEY,
                    ocr_run_id INTEGER UNIQUE REFERENCES engine_runs(id),
                    vlm_review_id INTEGER UNIQUE REFERENCES vlm_reviews(id),
                    verification_id INTEGER NOT NULL REFERENCES verification_runs(id),
                    pair_index INTEGER NOT NULL CHECK(pair_index >= 0),
                    frame_sha256 TEXT NOT NULL CHECK(length(frame_sha256) = 64),
                    crop_sha256 TEXT NOT NULL CHECK(length(crop_sha256) = 64),
                    crop_path TEXT NOT NULL,
                    x INTEGER NOT NULL CHECK(x >= 0), y INTEGER NOT NULL CHECK(y >= 0),
                    width INTEGER NOT NULL CHECK(width > 0), height INTEGER NOT NULL CHECK(height > 0),
                    scale INTEGER NOT NULL CHECK(scale > 0),
                    CHECK((ocr_run_id IS NULL) != (vlm_review_id IS NULL))
                )""")
                connection.execute("PRAGMA user_version = 14")
            if version < 15:
                self._create_v15(connection)
                connection.execute("PRAGMA user_version = 15")

    @staticmethod
    def _create_v15(connection: sqlite3.Connection) -> None:
        """Add multi-line and ambiguous alignment evidence without rebinding v14.

        The old scalar columns and pair indexes stay untouched.  Migration only
        backfills one member row for each known non-NULL scalar, so an old 1:1
        pair and every decision that cites it retain exactly their old identity.
        Ambiguity alternatives are new evidence and are never inferred for old
        records during upgrade.
        """

        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS alignment_line_members (
                alignment_id INTEGER NOT NULL
                    REFERENCES alignments(id) ON DELETE RESTRICT,
                side TEXT NOT NULL CHECK(side IN ('primary', 'secondary')),
                member_index INTEGER NOT NULL CHECK(member_index >= 0),
                line_index INTEGER NOT NULL CHECK(line_index >= 0),
                UNIQUE(alignment_id, side, member_index),
                UNIQUE(alignment_id, side, line_index)
            );

            CREATE TABLE IF NOT EXISTS alignment_ambiguities (
                alignment_id INTEGER PRIMARY KEY
                    REFERENCES alignments(id) ON DELETE RESTRICT,
                reason TEXT NOT NULL,
                score_margin REAL NOT NULL CHECK(score_margin >= 0)
            );

            CREATE TABLE IF NOT EXISTS alignment_alternatives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alignment_id INTEGER NOT NULL
                    REFERENCES alignment_ambiguities(alignment_id) ON DELETE RESTRICT,
                alternative_index INTEGER NOT NULL CHECK(alternative_index >= 0),
                score REAL NOT NULL,
                text_similarity REAL NOT NULL,
                cross_axis_distance REAL NOT NULL,
                UNIQUE(alignment_id, alternative_index)
            );

            CREATE TABLE IF NOT EXISTS alignment_alternative_members (
                alternative_id INTEGER NOT NULL
                    REFERENCES alignment_alternatives(id) ON DELETE RESTRICT,
                side TEXT NOT NULL CHECK(side IN ('primary', 'secondary')),
                member_index INTEGER NOT NULL CHECK(member_index >= 0),
                line_index INTEGER NOT NULL CHECK(line_index >= 0),
                UNIQUE(alternative_id, side, member_index),
                UNIQUE(alternative_id, side, line_index)
            );

            CREATE INDEX IF NOT EXISTS idx_alignment_members_alignment
                ON alignment_line_members(alignment_id, side, member_index);
            CREATE INDEX IF NOT EXISTS idx_alignment_alternatives_alignment
                ON alignment_alternatives(alignment_id, alternative_index);

            INSERT OR IGNORE INTO alignment_line_members(
                alignment_id, side, member_index, line_index
            )
            SELECT id, 'primary', 0, primary_line_index
            FROM alignments WHERE primary_line_index IS NOT NULL;
            INSERT OR IGNORE INTO alignment_line_members(
                alignment_id, side, member_index, line_index
            )
            SELECT id, 'secondary', 0, secondary_line_index
            FROM alignments WHERE secondary_line_index IS NOT NULL;
            """
        )

    @staticmethod
    def _create_v11(connection: sqlite3.Connection) -> None:
        """Record the digest of each rendered page image.

        Until v11 the store only knew where a page's raster lived and how big it
        was. It never recorded *what was in it*, so a replaced or re-rendered page
        could pass every check while no longer being the image anyone verified.
        v11 adds per-page SHA-256, byte size, render parameters and an evidence
        version, all written at import time and never overwritten by repair.

        Columns are nullable so projects imported before v11 keep opening: a NULL
        digest means "evidence from before v11", reported by the audit, not an
        error. ``scribeforge rehash-pages`` fills the gap.
        """

        connection.executescript(
            """
            ALTER TABLE pages ADD COLUMN page_asset_sha256 TEXT;
            ALTER TABLE pages ADD COLUMN page_asset_byte_size INTEGER CHECK(
                page_asset_byte_size IS NULL OR page_asset_byte_size > 0
            );
            ALTER TABLE pages ADD COLUMN page_asset_parameters_json TEXT;
            ALTER TABLE pages ADD COLUMN page_asset_version INTEGER CHECK(
                page_asset_version IS NULL OR page_asset_version > 0
            );
            """
        )

    @staticmethod
    def _create_v12(connection: sqlite3.Connection) -> None:
        """Persist whether each OCR line's worker reported token data.

        Existing v11 rows with token records are known to contain token evidence.
        A line with no token rows remains unknown: older schemas collapsed a
        missing token field and an explicit empty list, so migration must not
        invent certainty for those rows.
        """

        connection.executescript(
            """
            ALTER TABLE ocr_lines ADD COLUMN tokens_reported INTEGER NOT NULL
                DEFAULT 0 CHECK(tokens_reported IN (0, 1));
            UPDATE ocr_lines
            SET tokens_reported = 1
            WHERE EXISTS (
                SELECT 1 FROM ocr_tokens WHERE ocr_tokens.line_id = ocr_lines.id
            );
            """
        )

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
    def _create_v8(connection: sqlite3.Connection) -> None:
        """Record what an OCR run actually read: a page, or one region of it.

        A repair round re-reads a single disputed line from an enlarged crop. That
        reading is evidence in its own right and must be storable without
        pretending it came from a whole page: a NULL scope means "the whole page",
        ``pair:<n>`` means the line of that aligned pair. Without it, re-reads are
        either thrown away (so consensus cannot cite them) or appended as fake
        page runs (so "the newest run for engine X" silently becomes a crop).
        """

        connection.executescript(
            """
            ALTER TABLE engine_runs ADD COLUMN scope TEXT;
            """
        )

    @staticmethod
    def _create_v10(connection: sqlite3.Connection) -> None:
        """Store the evidence behind a page role, and where a chapter title came from.

        ``page_structure.ink_ratio`` is the measurement that separates a blank leaf
        from a plate: textless pages carry no text either way, and only the raster
        says which one it is (issues #87 and #74).

        ``chapters.source`` records whether a title came from the book's own
        contents page (the author's list, anchored to a physical page) or from a
        heading found on one page. They are not equally strong evidence, and an
        export that mixes them should say which is which.
        """

        connection.executescript(
            """
            ALTER TABLE page_structure ADD COLUMN ink_ratio REAL CHECK(
                ink_ratio IS NULL OR (ink_ratio >= 0.0 AND ink_ratio <= 1.0)
            );
            ALTER TABLE chapters ADD COLUMN source TEXT NOT NULL DEFAULT 'heading' CHECK(
                source IN ('toc', 'heading', 'manual')
            );
            """
        )

    @staticmethod
    def _create_v9(connection: sqlite3.Connection) -> None:
        """Record the risk a decision was made under, and explicit blank pages.

        ``decisions.risk_score`` keeps the audit trail #75 asks for: two years later
        the question "why was this line accepted without a person?" is answered from
        the row itself, not from re-running heuristics that have since changed.

        ``page_confirmations`` exists because zero alignment pairs is ambiguous
        (#74): a blank leaf, an illustration, a crashed worker and a page the engines
        both failed to read all look identical in the OCR tables. The only way to
        tell them apart is to record that somebody (or a measurement) said so.
        """

        connection.executescript(
            """
            ALTER TABLE decisions ADD COLUMN risk_score REAL;
            ALTER TABLE decisions ADD COLUMN risk_reasons_json TEXT NOT NULL DEFAULT '[]';

            -- The two signals the acceptance gate reads. They are part of the risk
            -- assessment, so they belong with it rather than being recomputed from
            -- evidence a later heuristic change may interpret differently.
            ALTER TABLE risks ADD COLUMN min_confidence REAL;
            ALTER TABLE risks ADD COLUMN content_score REAL NOT NULL DEFAULT 0.0;

            CREATE TABLE IF NOT EXISTS page_confirmations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE RESTRICT,
                kind TEXT NOT NULL CHECK(kind IN ('blank', 'non_text')),
                actor TEXT NOT NULL CHECK(actor IN ('human', 'evidence')),
                reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
                ink_ratio REAL CHECK(ink_ratio IS NULL OR (ink_ratio >= 0 AND ink_ratio <= 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_page_confirmations_page
                ON page_confirmations(page_id, id);
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
        *,
        page_asset: PageAssetEvidence | None = None,
    ) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pages(
                    project_id, page_index, source_image, width, height,
                    page_asset_sha256, page_asset_byte_size,
                    page_asset_parameters_json, page_asset_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    page_index,
                    source_image,
                    width,
                    height,
                    page_asset.sha256 if page_asset is not None else None,
                    page_asset.byte_size if page_asset is not None else None,
                    (
                        json.dumps(page_asset.as_render_parameters(), sort_keys=True)
                        if page_asset is not None
                        else None
                    ),
                    page_asset.version if page_asset is not None else None,
                ),
            )
            return self._last_id(cursor)

    def rehash_page_asset(
        self, page_id: int, *, require_missing: bool = False
    ) -> PageAssetEvidence | None:
        """Re-measure one persisted page's raster and store its digest.

        Returns the newly recorded evidence, or None when the raster is missing
        (the caller decides whether that is an error). Never overwrites evidence
        that already exists: a changed page must not launder itself through here.
        ``require_missing`` rejects a digest another writer added since enumeration.
        """

        with self._connection() as connection:
            found = connection.execute(
                """
                SELECT source_image, page_asset_sha256, page_asset_byte_size,
                       page_asset_parameters_json, page_asset_version
                FROM pages
                WHERE id = ?
                """,
                (page_id,),
            ).fetchone()
        if found is None:
            return None
        if require_missing and found["page_asset_sha256"] is not None:
            raise PageAssetError(
                f"page {page_id} evidence changed during rehash; retry after audit"
            )
        existing = parse_page_asset_evidence(
            found["page_asset_sha256"],
            found["page_asset_byte_size"],
            found["page_asset_parameters_json"],
            found["page_asset_version"],
        )
        path = Path(str(found["source_image"])).expanduser()
        if not path.is_file():
            return None
        actual, byte_size = _measure_page_asset(path)
        if byte_size == 0:
            raise PageAssetError(f"page {page_id} is empty; cannot record a page asset digest")
        measured = PageAssetEvidence(
            sha256=actual,
            byte_size=byte_size,
            render_parameters={},
            version=PAGE_ASSET_VERSION,
        )
        if existing is not None:
            if existing.sha256 != actual:
                raise PageAssetError(
                    f"page {page_id} does not match its recorded digest; "
                    "restore it from a verified backup instead of rehashing"
                )
            if existing.byte_size != byte_size:
                raise PageAssetError(
                    f"page {page_id} recorded evidence is incomplete or inconsistent"
                )
            return existing
        if _measure_page_asset(path) != (actual, byte_size):
            raise PageAssetError(f"page {page_id} raster changed during rehash; retry after audit")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE pages
                SET page_asset_sha256 = ?, page_asset_byte_size = ?,
                    page_asset_parameters_json = ?, page_asset_version = ?
                WHERE id = ? AND page_asset_sha256 IS NULL
                  AND page_asset_byte_size IS NULL AND page_asset_parameters_json IS NULL
                  AND page_asset_version IS NULL AND source_image = ?
                """,
                (
                    actual,
                    byte_size,
                    json.dumps({}, sort_keys=True),
                    PAGE_ASSET_VERSION,
                    page_id,
                    found["source_image"],
                ),
            )
            if cursor.rowcount != 1:
                raise PageAssetError(
                    f"page {page_id} evidence or source path changed during rehash; retry after audit"
                )
        return measured

    def rehash_missing_page_assets(self) -> tuple[int, int]:
        """Re-measure every page whose raster digest was never recorded.

        Returns ``(rehashed, missing)``: the number of pages that got a digest
        back, and the number whose raster file is no longer on disk. Existing
        digests are never overwritten.
        """

        rehashed = 0
        missing = 0
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, source_image FROM pages
                WHERE page_asset_sha256 IS NULL
                ORDER BY id
                """
            ).fetchall()
        for row in rows:
            page_id = int(row["id"])
            evidence = self.rehash_page_asset(page_id, require_missing=True)
            if evidence is None:
                missing += 1
            else:
                rehashed += 1
        return rehashed, missing

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
                SELECT id, project_id, page_index, source_image, width, height,
                       page_asset_sha256, page_asset_byte_size,
                       page_asset_parameters_json, page_asset_version
                FROM pages
                WHERE project_id = ?
                ORDER BY page_index
                """,
                (project_id,),
            ).fetchall()
            return tuple(_page_record(row) for row in rows)

    @staticmethod
    def _require_current_page(
        connection: sqlite3.Connection, project_id: int, page: PageRecord
    ) -> None:
        row = connection.execute(
            """
            SELECT id, project_id, page_index, source_image, width, height,
                   page_asset_sha256, page_asset_byte_size,
                   page_asset_parameters_json, page_asset_version
            FROM pages WHERE id = ? AND project_id = ?
            """,
            (page.id, project_id),
        ).fetchone()
        if row is None or _page_record(row) != page:
            raise ValueError(
                "page record does not match the current project evidence; "
                "reload the page before processing"
            )

    def require_current_page(self, project_id: int, page: PageRecord) -> None:
        """Reject a page from another project or a stale page-evidence snapshot."""
        with self._connection() as connection:
            self._require_current_page(connection, project_id, page)

    def latest_engine_run(self, project_id: int, page_index: int, engine: str) -> int:
        """Resolve the newest whole-page OCR run for one engine on one page.

        Region re-reads (repair rounds) are deliberately excluded. They are stored
        as runs of the same page, but their boxes are crop-local and their text is
        one line: if one of them could win here, "the newest reading of this page"
        would silently become a single line of a single crop, and every consumer —
        alignment, furniture detection, assembly — would read a fragment as a page.
        """

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
                  AND engine_runs.scope IS NULL
                ORDER BY engine_runs.id DESC
                LIMIT 1
                """,
                (project_id, page_index, engine),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown OCR run for engine {engine} on page {page_index}")
            return int(row["id"])

    def list_scoped_run_ids(self, project_id: int, page_index: int, scope: str) -> tuple[int, ...]:
        """Return the runs of one region of a page, oldest first.

        Region scopes are how a repair round's re-reads stay distinguishable from
        whole-page runs: the length of this list is also how many times the pair
        has already been re-read, which is what escalates the next attempt.
        """

        if not scope.strip():
            raise ValueError("a run scope must be non-empty")
        with self._connection() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT engine_runs.id
                FROM engine_runs
                JOIN pages ON pages.id = engine_runs.page_id
                WHERE pages.project_id = ?
                  AND pages.page_index = ?
                  AND engine_runs.scope = ?
                ORDER BY engine_runs.id
                """,
                (project_id, page_index, scope),
            ).fetchall()
            return tuple(int(row["id"]) for row in rows)

    def latest_decision_actors(self, project_id: int) -> dict[tuple[int, int], str]:
        """Return the newest decision actor for every decided page pair.

        Used to report how much of a book was accepted without a human: the
        operator needs that number to decide whether to run another repair round
        or merely spot-check the result.
        """

        with self._connection() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT pages.page_index, decisions.pair_index, decisions.actor
                FROM decisions
                JOIN pages ON pages.id = decisions.page_id
                WHERE pages.project_id = ?
                  AND decisions.id = (
                      SELECT MAX(newer.id)
                      FROM decisions AS newer
                      WHERE newer.page_id = decisions.page_id
                        AND newer.pair_index = decisions.pair_index
                  )
                """,
                (project_id,),
            ).fetchall()
            return {
                (int(row["page_index"]), int(row["pair_index"])): str(row["actor"]) for row in rows
            }

    def latest_verification_id(self, project_id: int, page_index: int) -> int:
        """Resolve the latest run, refusing ambiguous historical decisions.

        Raw verification and decision history remain available through their
        dedicated methods; this guard applies when selecting the active context.
        """
        with self._connection() as connection:
            # The run and decisions must be examined in the same read snapshot.
            connection.execute("BEGIN")
            self._require_project(connection, project_id)
            row = connection.execute(
                """
                SELECT v.id, v.page_id,
                       (SELECT COUNT(*) FROM verification_runs AS all_runs
                        WHERE all_runs.page_id = v.page_id) AS run_count
                FROM verification_runs AS v
                JOIN pages ON pages.id = v.page_id
                WHERE pages.project_id = ? AND pages.page_index = ?
                ORDER BY v.id DESC LIMIT 1
                """,
                (project_id, page_index),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown verification run for page {page_index}")
            verification_id = int(row["id"])
            findings = inspect_latest_decisions(
                connection,
                int(row["page_id"]),
                verification_id,
                multiple_runs=int(row["run_count"]) > 1,
            )
            for decision, status in findings:
                if status == "ambiguous":
                    raise ValueError(
                        f"ambiguous verification batch for decision on project {project_id}, "
                        f"page {page_index}, pair {decision['pair_index']}; "
                        "preserve the history and create a new project for reprocessing"
                    )
            return verification_id

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
                None if page_index is None else self._page_id(connection, project_id, page_index)
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
        """Claim one job under a project-scoped, crash-released OS lock.

        If a process dies, the operating system drops its lock and a new process
        can reclaim the persisted ``running`` job; its attempt count is retained.
        """

        with self._ownership_guard:
            claimed = self.load_job(job_id)
            project_id = claimed.project_id
            page_index = claimed.page_index
            key = (project_id, page_index)
            if self._lock_key_conflicts(project_id, page_index):
                active_job_id = self._active_job_ids.get(key)
                if active_job_id is None:
                    active_job_id = next(
                        job_id_
                        for (locked_project, _), job_id_ in self._active_job_ids.items()
                        if locked_project == project_id
                    )
                raise JobAlreadyRunningError(f"this store already owns running job {active_job_id}")
            self._writer_locks[key] = self._acquire_project_writer_lock(project_id, page_index)
            try:
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
                    running = self._job_record(self._job_row(connection, job_id))
                self._active_job_ids[key] = job_id
                return running
            except BaseException:
                self._release_project_writer_lock(project_id, page_index)
                raise

    def _lock_key_conflicts(self, project_id: int, page_index: int | None) -> bool:
        """A book-level lock (page None) conflicts with every page of the project."""
        if page_index is None:
            return any(locked_project == project_id for locked_project, _ in self._writer_locks)
        return (project_id, None) in self._writer_locks or (
            project_id,
            page_index,
        ) in self._writer_locks

    def _require_job_owner(self, job_id: int, project_id: int, page_index: int | None) -> None:
        key = (project_id, page_index)
        if key not in self._writer_locks or self._active_job_ids.get(key) != job_id:
            raise JobOwnershipError(f"this store does not own running job {job_id}")

    def complete_job(self, job_id: int) -> JobRecord:
        with self._ownership_guard:
            with self._connection() as connection:
                current = self._job_record(self._job_row(connection, job_id))
                if current.state is not JobState.RUNNING:
                    raise ValueError("job must be running before it can succeed")
                self._require_job_owner(job_id, current.project_id, current.page_index)
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = 'succeeded', finished_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (job_id,),
                )
                completed = self._job_record(self._job_row(connection, job_id))
            self._release_project_writer_lock(current.project_id, current.page_index)
            return completed

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
        with self._ownership_guard:
            with self._connection() as connection:
                current = self._job_record(self._job_row(connection, job_id))
                if current.state is not JobState.RUNNING:
                    raise ValueError("job must be running before it can fail")
                self._require_job_owner(job_id, current.project_id, current.page_index)
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = 'failed', last_error = ?, finished_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (error, job_id),
                )
                failed = self._job_record(self._job_row(connection, job_id))
            self._release_project_writer_lock(current.project_id, current.page_index)
            return failed

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
        expected_page: PageRecord | None = None,
    ) -> int:
        """Persist the preprocessing evidence for one page, replacing any prior run.

        Re-running with different settings must overwrite rather than accumulate:
        only the raster the OCR engines actually consumed is meaningful, and two
        conflicting rows would leave no way to tell which one that was.
        """

        if output_width <= 0 or output_height <= 0:
            raise ValueError("preprocessed page dimensions must be positive")
        with self._connection() as connection:
            if expected_page is not None:
                connection.execute("BEGIN IMMEDIATE")
                self._require_current_page(connection, project_id, expected_page)
                if expected_page.page_index != page_index:
                    raise ValueError("page record does not match preprocessing page index")
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

    def load_preprocess(self, project_id: int, page_index: int) -> PreprocessRecord | None:
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
                        sequence_number, sequence_ordinal, ink_ratio
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(page_id) DO UPDATE SET
                        role = excluded.role,
                        printed_label = excluded.printed_label,
                        numbering_scheme = excluded.numbering_scheme,
                        sequence_number = excluded.sequence_number,
                        sequence_ordinal = excluded.sequence_ordinal,
                        ink_ratio = excluded.ink_ratio,
                        created_at = CURRENT_TIMESTAMP
                    """,
                    (
                        page_id,
                        structure.role.value,
                        structure.label.printed_label,
                        structure.label.scheme.value,
                        structure.label.sequence,
                        structure.label.sequence_ordinal,
                        structure.ink_ratio,
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
                        project_id, ordinal, level, title, start_page_index,
                        printed_page, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        chapter.ordinal,
                        chapter.level,
                        chapter.title,
                        chapter.start_page_index,
                        chapter.printed_page,
                        chapter.source,
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
                       page_structure.sequence_ordinal AS sequence_ordinal,
                       page_structure.ink_ratio AS ink_ratio
                FROM pages
                LEFT JOIN page_structure ON page_structure.page_id = pages.id
                WHERE pages.project_id = ?
                ORDER BY pages.page_index
                """,
                (project_id,),
            ).fetchall()
            chapter_rows = connection.execute(
                """
                SELECT ordinal, level, title, start_page_index, printed_page, source
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
                sequence=(None if row["sequence_number"] is None else int(row["sequence_number"])),
                sequence_ordinal=(
                    None if row["sequence_ordinal"] is None else int(row["sequence_ordinal"])
                ),
            )
            pages.append(
                PageStructure(
                    page_index=int(row["page_index"]),
                    role=(PageRole.UNKNOWN if row["role"] is None else PageRole(str(row["role"]))),
                    label=label,
                    ink_ratio=(None if row["ink_ratio"] is None else float(row["ink_ratio"])),
                )
            )

        chapters = tuple(
            Chapter(
                ordinal=int(row["ordinal"]),
                level=int(row["level"]),
                title=str(row["title"]),
                start_page_index=int(row["start_page_index"]),
                printed_page=str(row["printed_page"]),
                source=str(row["source"]),
            )
            for row in chapter_rows
        )
        return BookStructure(tuple(pages), chapters)

    def record_ocr(
        self,
        project_id: int,
        result: PageOCRResult,
        *,
        scope: str | None = None,
        frame: OCRInputFrame | None = None,
        crop_input: CropInputEvidence | None = None,
        expected_page: PageRecord | None = None,
    ) -> int:
        if scope is not None and not scope.strip():
            raise ValueError("an OCR run scope must be non-empty when given")
        with self._connection() as connection:
            if expected_page is not None:
                connection.execute("BEGIN IMMEDIATE")
                self._require_current_page(connection, project_id, expected_page)
                if expected_page.page_index != result.page_index:
                    raise ValueError("page record does not match OCR result page index")
            page_id = self._page_id(connection, project_id, result.page_index)
            cursor = connection.execute(
                """
                INSERT INTO engine_runs(page_id, engine, engine_version, direction, scope)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    page_id,
                    result.engine,
                    result.engine_version,
                    result.direction.value if result.direction_declared else None,
                    scope,
                ),
            )
            run_id = self._last_id(cursor)
            if crop_input is not None:
                self._insert_crop_input(connection, crop_input, ocr_run_id=run_id)
            if frame is not None:
                connection.execute(
                    "INSERT INTO ocr_input_frames(run_id, kind, sha256, width, height) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (run_id, frame.kind, frame.sha256, frame.width, frame.height),
                )
            for line_index, line in enumerate(result.lines):
                line_cursor = connection.execute(
                    """
                    INSERT INTO ocr_lines(
                        run_id, line_index, text, x, y, width, height, confidence,
                        tokens_reported
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        int(line.tokens is not None),
                    ),
                )
                line_id = self._last_id(line_cursor)
                for token_index, token in enumerate(line.tokens or ()):
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

    def load_ocr_frame(self, run_id: int) -> OCRInputFrame | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT kind, sha256, width, height FROM ocr_input_frames WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            return OCRInputFrame(
                str(row["kind"]), str(row["sha256"]), int(row["width"]), int(row["height"])
            )

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
                SELECT id, line_index, text, x, y, width, height, confidence,
                       tokens_reported
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
                token_evidence: tuple[OCRToken, ...] | None = tuple(
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
                if not bool(line_row["tokens_reported"]) and not token_evidence:
                    token_evidence = None
                lines.append(
                    OCRLine(
                        text=str(line_row["text"]),
                        box=BoundingBox(
                            float(line_row["x"]),
                            float(line_row["y"]),
                            float(line_row["width"]),
                            float(line_row["height"]),
                        ),
                        tokens=token_evidence,
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
    def _engine_evidence(connection: sqlite3.Connection, run_id: int) -> sqlite3.Row:
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
        connection: sqlite3.Connection,
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
        line_counts = {
            side: int(
                connection.execute(
                    "SELECT COUNT(*) FROM ocr_lines WHERE run_id = ?",
                    (int(run["id"]),),
                ).fetchone()[0]
            )
            for side, run in (("primary", primary), ("secondary", secondary))
        }
        used: dict[str, set[int]] = {"primary": set(), "secondary": set()}
        for pair in alignment.pairs:
            for side, indexes in (
                ("primary", pair.primary_line_indexes),
                ("secondary", pair.secondary_line_indexes),
            ):
                if any(index >= line_counts[side] for index in indexes):
                    raise ValueError(f"{side} alignment line index is outside its OCR run")
                if used[side] & set(indexes):
                    raise ValueError(f"{side} OCR line occurs in more than one alignment pair")
                used[side].update(indexes)
            if pair.ambiguity is not None:
                for alternative in pair.ambiguity.alternatives:
                    if not set(alternative.primary_line_indexes) <= set(
                        pair.primary_line_indexes
                    ) or not set(alternative.secondary_line_indexes) <= set(
                        pair.secondary_line_indexes
                    ):
                        raise ValueError("ambiguity alternative escapes its alignment region")

    def record_verification(
        self,
        primary_run_id: int,
        secondary_run_id: int,
        alignment: PageAlignment,
        risks: tuple[PairRisk, ...],
        candidates: tuple[ReviewCandidate, ...],
    ) -> int:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            primary = self._engine_evidence(connection, primary_run_id)
            secondary = self._engine_evidence(connection, secondary_run_id)
            self._validate_verification(
                connection, primary, secondary, alignment, risks, candidates
            )
            if (
                connection.execute(
                    "SELECT 1 FROM decisions WHERE page_id = ? LIMIT 1", (primary["page_id"],)
                ).fetchone()
                is not None
            ):
                raise ValueError(
                    "verification cannot be replaced after decisions exist; create a new project"
                )
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
                for side, indexes in (
                    ("primary", pair.primary_line_indexes),
                    ("secondary", pair.secondary_line_indexes),
                ):
                    for member_index, line_index in enumerate(indexes):
                        connection.execute(
                            """
                            INSERT INTO alignment_line_members(
                                alignment_id, side, member_index, line_index
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (alignment_id, side, member_index, line_index),
                        )
                if pair.ambiguity is not None:
                    connection.execute(
                        """
                        INSERT INTO alignment_ambiguities(
                            alignment_id, reason, score_margin
                        ) VALUES (?, ?, ?)
                        """,
                        (
                            alignment_id,
                            pair.ambiguity.reason.value,
                            pair.ambiguity.score_margin,
                        ),
                    )
                    for alternative_index, alternative in enumerate(pair.ambiguity.alternatives):
                        alternative_cursor = connection.execute(
                            """
                            INSERT INTO alignment_alternatives(
                                alignment_id, alternative_index, score,
                                text_similarity, cross_axis_distance
                            ) VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                alignment_id,
                                alternative_index,
                                alternative.score,
                                alternative.text_similarity,
                                alternative.cross_axis_distance,
                            ),
                        )
                        alternative_id = self._last_id(alternative_cursor)
                        for side, indexes in (
                            ("primary", alternative.primary_line_indexes),
                            ("secondary", alternative.secondary_line_indexes),
                        ):
                            for member_index, line_index in enumerate(indexes):
                                connection.execute(
                                    """
                                    INSERT INTO alignment_alternative_members(
                                        alternative_id, side, member_index, line_index
                                    ) VALUES (?, ?, ?, ?)
                                    """,
                                    (alternative_id, side, member_index, line_index),
                                )
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
                    """
                    INSERT INTO risks(
                        alignment_id, score, reasons_json, min_confidence, content_score
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        alignment_id,
                        risk.score,
                        json.dumps([reason.value for reason in risk.reasons]),
                        risk.min_confidence,
                        risk.content_score,
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

    @staticmethod
    def _stored_line_members(
        connection: sqlite3.Connection,
        alignment_id: int,
        scalar_index: int | None,
        side: str,
    ) -> tuple[int, ...]:
        rows = connection.execute(
            """
            SELECT line_index FROM alignment_line_members
            WHERE alignment_id = ? AND side = ?
            ORDER BY member_index
            """,
            (alignment_id, side),
        ).fetchall()
        if not rows:
            if scalar_index is None:
                return ()
            raise ValueError(
                f"stored alignment {alignment_id} is missing {side} membership evidence"
            )
        indexes = tuple(int(row["line_index"]) for row in rows)
        if scalar_index is None or indexes[0] != scalar_index:
            raise ValueError(
                f"stored alignment {alignment_id} has inconsistent {side} membership evidence"
            )
        return indexes

    @staticmethod
    def _stored_ambiguity(
        connection: sqlite3.Connection, alignment_id: int
    ) -> AlignmentAmbiguity | None:
        row = connection.execute(
            """
            SELECT reason, score_margin FROM alignment_ambiguities
            WHERE alignment_id = ?
            """,
            (alignment_id,),
        ).fetchone()
        if row is None:
            return None
        alternative_rows = connection.execute(
            """
            SELECT id, score, text_similarity, cross_axis_distance
            FROM alignment_alternatives
            WHERE alignment_id = ? ORDER BY alternative_index
            """,
            (alignment_id,),
        ).fetchall()
        alternatives: list[AlignmentAlternative] = []
        for alternative_row in alternative_rows:
            alternative_id = int(alternative_row["id"])
            member_rows = connection.execute(
                """
                SELECT side, member_index, line_index
                FROM alignment_alternative_members
                WHERE alternative_id = ? ORDER BY side, member_index
                """,
                (alternative_id,),
            ).fetchall()
            indexes: dict[str, tuple[int, ...]] = {"primary": (), "secondary": ()}
            for side in ("primary", "secondary"):
                indexes[side] = tuple(
                    int(member["line_index"]) for member in member_rows if member["side"] == side
                )
            alternatives.append(
                AlignmentAlternative(
                    primary_line_indexes=indexes["primary"],
                    secondary_line_indexes=indexes["secondary"],
                    score=float(alternative_row["score"]),
                    text_similarity=float(alternative_row["text_similarity"]),
                    cross_axis_distance=float(alternative_row["cross_axis_distance"]),
                )
            )
        return AlignmentAmbiguity(
            reason=AmbiguityReason(str(row["reason"])),
            score_margin=float(row["score_margin"]),
            alternatives=tuple(alternatives),
        )

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
                primary_scalar = (
                    None if row["primary_line_index"] is None else int(row["primary_line_index"])
                )
                secondary_scalar = (
                    None
                    if row["secondary_line_index"] is None
                    else int(row["secondary_line_index"])
                )
                primary_indexes = self._stored_line_members(
                    connection, alignment_id, primary_scalar, "primary"
                )
                secondary_indexes = self._stored_line_members(
                    connection, alignment_id, secondary_scalar, "secondary"
                )
                ambiguity = self._stored_ambiguity(connection, alignment_id)
                pairs.append(
                    LineAlignment(
                        primary_line_index=primary_scalar,
                        secondary_line_index=secondary_scalar,
                        text_similarity=float(row["text_similarity"]),
                        cross_axis_distance=(
                            None
                            if row["vertical_distance"] is None
                            else float(row["vertical_distance"])
                        ),
                        conflicts=conflicts,
                        primary_line_indexes=primary_indexes,
                        secondary_line_indexes=secondary_indexes,
                        status=(
                            AlignmentStatus.AMBIGUOUS
                            if ambiguity is not None
                            else AlignmentStatus.MATCHED
                        ),
                        ambiguity=ambiguity,
                    )
                )
                risk_row = connection.execute(
                    """
                    SELECT score, reasons_json, min_confidence, content_score
                    FROM risks WHERE alignment_id = ?
                    """,
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
                        min_confidence=(
                            None
                            if risk_row["min_confidence"] is None
                            else float(risk_row["min_confidence"])
                        ),
                        content_score=float(risk_row["content_score"]),
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
                        RiskReason(value) for value in self._json_strings(row["reasons_json"])
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

    def verification_page_record(self, project_id: int, verification_id: int) -> PageRecord:
        """Resolve a verification's page within the requested project."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT p.id, p.project_id, p.page_index, p.source_image, p.width, p.height,
                       p.page_asset_sha256, p.page_asset_byte_size,
                       p.page_asset_parameters_json, p.page_asset_version
                FROM verification_runs v JOIN pages p ON p.id = v.page_id
                WHERE v.id = ? AND p.project_id = ?
                """,
                (verification_id, project_id),
            ).fetchone()
        if row is None:
            raise ValueError("verification does not belong to the requested project")
        return _page_record(row)

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
        crop_input: CropInputEvidence | None = None,
        expected_page: PageRecord | None = None,
    ) -> int:
        if crop_input is not None and (
            crop_input.verification_id != verification_id
            or crop_input.pair_index != reading.pair_index
            or crop_input.crop_path != crop_path
            or crop_input.crop_sha256 != crop_sha256
        ):
            raise ValueError("VLM crop evidence does not match reading")
        if not crop_path.strip():
            raise ValueError("VLM review crop path is required")
        if not crop_sha256.strip():
            raise ValueError("VLM review crop SHA-256 is required")
        with self._connection() as connection:
            if expected_page is not None:
                connection.execute("BEGIN IMMEDIATE")
                self._require_current_page(connection, expected_page.project_id, expected_page)
                if expected_page.page_index != reading.page_index:
                    raise ValueError("page record does not match VLM reading page index")
                verification = connection.execute(
                    "SELECT page_id FROM verification_runs WHERE id = ?", (verification_id,)
                ).fetchone()
                if verification is None or verification["page_id"] != expected_page.id:
                    raise ValueError("page record does not match VLM verification page")
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
            review_id = self._last_id(cursor)
            if crop_input is not None:
                self._insert_crop_input(connection, crop_input, vlm_review_id=review_id)
            return review_id

    @staticmethod
    def _insert_crop_input(
        connection: sqlite3.Connection,
        evidence: CropInputEvidence,
        *,
        ocr_run_id: int | None = None,
        vlm_review_id: int | None = None,
    ) -> None:
        row = connection.execute(
            """SELECT v.page_id, f.sha256, f.width, f.height,
                    f.kind, f2.sha256 AS second_sha256, f2.width AS second_width,
                    f2.height AS second_height, f2.kind AS second_kind
            FROM verification_runs v
            JOIN ocr_input_frames f ON f.run_id = v.primary_run_id
            JOIN ocr_input_frames f2 ON f2.run_id = v.secondary_run_id
            JOIN alignments a ON a.verification_run_id = v.id AND a.pair_index = ?
            WHERE v.id = ?""",
            (evidence.pair_index, evidence.verification_id),
        ).fetchone()
        if (
            row is None
            or row["sha256"] != evidence.frame_sha256
            or (row["sha256"], row["width"], row["height"], row["kind"])
            != (row["second_sha256"], row["second_width"], row["second_height"], row["second_kind"])
        ):
            raise ValueError("crop frame does not match verification evidence")
        if evidence.x + evidence.width > int(row["width"]) or evidence.y + evidence.height > int(
            row["height"]
        ):
            raise ValueError("crop geometry exceeds frame")
        if ocr_run_id is not None:
            run = connection.execute(
                "SELECT page_id, scope FROM engine_runs WHERE id = ?", (ocr_run_id,)
            ).fetchone()
            if run is None or run["page_id"] != row["page_id"]:
                raise ValueError("crop OCR run page does not match verification page")
            if run["scope"] != f"pair:{evidence.pair_index}":
                raise ValueError("crop OCR scope does not match verification pair")
        connection.execute(
            """INSERT INTO crop_input_evidence
            (ocr_run_id, vlm_review_id, verification_id, pair_index, frame_sha256,
             crop_sha256, crop_path, x, y, width, height, scale)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ocr_run_id,
                vlm_review_id,
                evidence.verification_id,
                evidence.pair_index,
                evidence.frame_sha256,
                evidence.crop_sha256,
                evidence.crop_path,
                evidence.x,
                evidence.y,
                evidence.width,
                evidence.height,
                evidence.scale,
            ),
        )

    def load_vlm_crop_input(self, review_id: int) -> CropInputEvidence | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM crop_input_evidence WHERE vlm_review_id = ?", (review_id,)
            ).fetchone()
            if row is None:
                return None
            return CropInputEvidence(
                int(row["verification_id"]),
                int(row["pair_index"]),
                str(row["frame_sha256"]),
                str(row["crop_sha256"]),
                str(row["crop_path"]),
                int(row["x"]),
                int(row["y"]),
                int(row["width"]),
                int(row["height"]),
                int(row["scale"]),
            )

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

    def record_page_confirmation(
        self,
        *,
        project_id: int,
        page_index: int,
        kind: str,
        actor: str,
        reason: str,
        ink_ratio: float | None = None,
    ) -> int:
        """Record that a page has no text, and on what authority.

        Append-only: a second confirmation of the same page adds a row instead of
        rewriting the first, so the history of "somebody said this was blank" stays
        intact even if a later reader disagrees.
        """

        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, page_index)
            cursor = connection.execute(
                """
                INSERT INTO page_confirmations(page_id, kind, actor, reason, ink_ratio)
                VALUES (?, ?, ?, ?, ?)
                """,
                (page_id, kind, actor, reason, ink_ratio),
            )
            return self._last_id(cursor)

    def list_page_confirmations(
        self, project_id: int, page_index: int
    ) -> tuple[PageConfirmation, ...]:
        with self._connection() as connection:
            page_id = self._page_id(connection, project_id, page_index)
            rows = connection.execute(
                """
                SELECT id, kind, actor, reason, ink_ratio, created_at
                FROM page_confirmations
                WHERE page_id = ?
                ORDER BY id
                """,
                (page_id,),
            ).fetchall()
            return tuple(
                PageConfirmation(
                    id=int(row["id"]),
                    page_index=page_index,
                    kind=str(row["kind"]),
                    actor=str(row["actor"]),
                    reason=str(row["reason"]),
                    ink_ratio=(None if row["ink_ratio"] is None else float(row["ink_ratio"])),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            )

    def latest_page_confirmations(self, project_id: int) -> dict[int, PageConfirmation]:
        """Newest confirmation per confirmed page of a project."""

        with self._connection() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """
                SELECT pages.page_index, confirmations.id, confirmations.kind,
                       confirmations.actor, confirmations.reason,
                       confirmations.ink_ratio, confirmations.created_at
                FROM page_confirmations AS confirmations
                JOIN pages ON pages.id = confirmations.page_id
                WHERE pages.project_id = ?
                ORDER BY confirmations.id
                """,
                (project_id,),
            ).fetchall()
            latest: dict[int, PageConfirmation] = {}
            for row in rows:
                page_index = int(row["page_index"])
                latest[page_index] = PageConfirmation(
                    id=int(row["id"]),
                    page_index=page_index,
                    kind=str(row["kind"]),
                    actor=str(row["actor"]),
                    reason=str(row["reason"]),
                    ink_ratio=(None if row["ink_ratio"] is None else float(row["ink_ratio"])),
                    created_at=str(row["created_at"]),
                )
            return latest

    def append_decision(
        self,
        *,
        project_id: int,
        page_index: int,
        pair_index: int,
        selected_text: str,
        actor: str,
        evidence_refs: tuple[str, ...],
        risk_score: float | None = None,
        risk_reasons: tuple[str, ...] = (),
        expected_page: PageRecord | None = None,
        expected_verification_id: int | None = None,
        require_undecided: bool = False,
    ) -> int:
        """Append an accepted decision, with the risk it was accepted under.

        ``risk_score`` is stored on the row rather than recomputed at read time: an
        audit must see what the decision was made on, not what today's heuristics
        would say about the same evidence. ``require_undecided`` atomically refuses
        to append if any decision already exists for this page and pair; explicit
        human corrections can omit it to preserve the append-only revision history.
        """

        if risk_score is not None and not 0.0 <= risk_score <= 1.0:
            raise ValueError("risk score must be between 0 and 1")
        with self._connection() as connection:
            if (
                expected_page is not None
                or expected_verification_id is not None
                or require_undecided
            ):
                connection.execute("BEGIN IMMEDIATE")
            if expected_page is not None:
                self._require_current_page(connection, project_id, expected_page)
                if expected_page.page_index != page_index:
                    raise ValueError("page record does not match decision page index")
            page_id = self._page_id(connection, project_id, page_index)
            if expected_verification_id is not None:
                verification = connection.execute(
                    """SELECT v.page_id FROM verification_runs v
                       JOIN alignments a ON a.verification_run_id=v.id
                       WHERE v.id=? AND a.pair_index=?""",
                    (expected_verification_id, pair_index),
                ).fetchone()
                if verification is None or verification["page_id"] != page_id:
                    raise ValueError("verification does not match decision page or pair")
                latest = connection.execute(
                    "SELECT MAX(id) FROM verification_runs WHERE page_id = ?", (page_id,)
                ).fetchone()[0]
                if latest != expected_verification_id:
                    raise ValueError("verification changed; reload the latest page verification")
                refs = [ref for ref in evidence_refs if ref.startswith("verification_run:")]
                if refs != [f"verification_run:{expected_verification_id}"]:
                    raise ValueError(
                        "verification citation does not match the expected verification"
                    )
            if (
                require_undecided
                and connection.execute(
                    "SELECT 1 FROM decisions WHERE page_id = ? AND pair_index = ? LIMIT 1",
                    (page_id, pair_index),
                ).fetchone()
                is not None
            ):
                raise DecisionAlreadyExists("a decision already exists for this page and pair")
            cursor = connection.execute(
                """
                INSERT INTO decisions(
                    page_id, pair_index, selected_text, actor, evidence_refs_json,
                    risk_score, risk_reasons_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    page_id,
                    pair_index,
                    selected_text,
                    actor,
                    json.dumps(evidence_refs, ensure_ascii=False),
                    risk_score,
                    json.dumps(list(risk_reasons), ensure_ascii=False),
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
                SELECT id, selected_text, actor, evidence_refs_json, created_at,
                       risk_score, risk_reasons_json
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
                    risk_score=(None if row["risk_score"] is None else float(row["risk_score"])),
                    risk_reasons=self._json_strings(row["risk_reasons_json"]),
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
                SELECT id, selected_text, actor, evidence_refs_json, created_at,
                       risk_score, risk_reasons_json
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
                risk_score=(None if row["risk_score"] is None else float(row["risk_score"])),
                risk_reasons=self._json_strings(row["risk_reasons_json"]),
            )

    def latest_decision_for_verification(
        self, project_id: int, page_index: int, pair_index: int, *, verification_id: int
    ) -> DecisionRecord | None:
        """Newest decision for a page pair, but only if safely bound to one run.

        Fail closed (#150): the run and the decision are examined in the same
        read snapshot. Raises when ``verification_id`` is not the page's latest
        run, or when the newest decision cannot be attributed to it (no
        alignment row for the pair in this run, malformed or conflicting
        citations). A legacy decision with no citation is returned only when the
        page has exactly one verification run, where pair semantics cannot have
        changed; in a multi-run page it is ambiguous and rejected. Raw history
        stays readable through :meth:`list_decisions` and is never rewritten.
        """

        with self._connection() as connection:
            connection.execute("BEGIN")
            self._require_project(connection, project_id)
            page_id = self._page_id(connection, project_id, page_index)
            run = connection.execute(
                "SELECT page_id FROM verification_runs WHERE id = ?", (verification_id,)
            ).fetchone()
            if run is None or int(run["page_id"]) != page_id:
                raise ValueError(
                    f"verification {verification_id} does not belong to "
                    f"project {project_id} page {page_index}"
                )
            latest = connection.execute(
                "SELECT MAX(id) FROM verification_runs WHERE page_id = ?", (page_id,)
            ).fetchone()[0]
            if latest != verification_id:
                raise ValueError("verification changed; reload the latest page verification")
            run_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM verification_runs WHERE page_id = ?", (page_id,)
                ).fetchone()[0]
            )
            row = connection.execute(
                """
                SELECT id, selected_text, actor, evidence_refs_json, created_at,
                       risk_score, risk_reasons_json
                FROM decisions
                WHERE page_id = ? AND pair_index = ?
                ORDER BY id DESC LIMIT 1
                """,
                (page_id, pair_index),
            ).fetchone()
            if row is None:
                return None
            alignment = connection.execute(
                "SELECT 1 FROM alignments WHERE verification_run_id = ? AND pair_index = ?",
                (verification_id, pair_index),
            ).fetchone()
            try:
                decoded = json.loads(str(row["evidence_refs_json"]))
                if not isinstance(decoded, list):
                    raise TypeError("stored JSON list is invalid")
                refs = [str(ref) for ref in decoded if str(ref).startswith("verification_run:")]
            except (ValueError, TypeError):
                refs = None
            bound = refs is not None and refs == [f"verification_run:{verification_id}"]
            legacy_single_run = refs == [] and run_count == 1
            if alignment is None or not (bound or legacy_single_run):
                raise ValueError(
                    f"ambiguous verification batch for decision on project {project_id}, "
                    f"page {page_index}, pair {pair_index}; "
                    "preserve the history and create a new project for reprocessing"
                )
            return DecisionRecord(
                id=int(row["id"]),
                project_id=project_id,
                page_index=page_index,
                pair_index=pair_index,
                selected_text=str(row["selected_text"]),
                actor=str(row["actor"]),
                evidence_refs=self._json_strings(row["evidence_refs_json"]),
                created_at=str(row["created_at"]),
                risk_score=(None if row["risk_score"] is None else float(row["risk_score"])),
                risk_reasons=self._json_strings(row["risk_reasons_json"]),
            )
