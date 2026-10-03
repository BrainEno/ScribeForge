from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult


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


class SQLiteStore:
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
                PRAGMA user_version = 1;
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
                    evidence_refs=tuple(str(value) for value in json.loads(row["evidence_refs_json"])),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            )
