from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scribeforge.domain.alignment import (
    AlignmentConflict,
    ConflictKind,
    LineAlignment,
    PageAlignment,
)
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult
from scribeforge.domain.risk import PairRisk, ReviewCandidate, RiskReason


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


class SQLiteStore:
    SCHEMA_VERSION = 2

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
        return row

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
