from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from scribeforge.domain.page_asset import PageAssetEvidence


@dataclass(frozen=True, slots=True)
class AuditFinding:
    code: str
    severity: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, str]:
        result = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.path is not None:
            result["path"] = self.path
        return result


@dataclass(frozen=True, slots=True)
class ProjectAuditReport:
    store_path: str
    database_path: str
    database_readable: bool
    sqlite_integrity: str
    foreign_key_violations: int
    project_count: int
    page_count: int
    findings: tuple[AuditFinding, ...]

    @property
    def healthy(self) -> bool:
        return self.database_readable and not self.findings

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "store": self.store_path,
            "database": self.database_path,
            "healthy": self.healthy,
            "database_readable": self.database_readable,
            "sqlite_integrity": self.sqlite_integrity,
            "foreign_key_violations": self.foreign_key_violations,
            "project_count": self.project_count,
            "page_count": self.page_count,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def audit_project_store(store_path: Path) -> ProjectAuditReport:
    """Audit a project store using a read-only SQLite connection and no repairs."""
    root = store_path.expanduser().resolve()
    database = root / "project.sqlite3"
    findings: list[AuditFinding] = []

    if not database.is_file():
        findings.append(
            AuditFinding(
                "database_missing",
                "fatal",
                "Project database was not found.",
                str(database),
            )
        )
        return _report(root, database, False, "not_checked", 0, 0, 0, findings)

    try:
        database_uri = f"{database.as_uri()}?mode=ro"
        with closing(sqlite3.connect(database_uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity_results = [str(row[0]) for row in integrity_rows]
            integrity = "; ".join(integrity_results) if integrity_results else "no result"
            if integrity_results != ["ok"]:
                findings.append(
                    AuditFinding(
                        "database_integrity_failed",
                        "error",
                        f"SQLite integrity check reported: {integrity}",
                        str(database),
                    )
                )

            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            foreign_key_violations = len(foreign_keys)
            if foreign_key_violations:
                findings.append(
                    AuditFinding(
                        "foreign_key_violations",
                        "error",
                        f"SQLite reports {foreign_key_violations} foreign-key violation(s).",
                        str(database),
                    )
                )

            projects = connection.execute(
                """
                SELECT id, title, source_uri, source_sha256
                FROM projects
                ORDER BY id
                """
            ).fetchall()
            pages = connection.execute(
                """
                SELECT project_id, page_index, source_image,
                       page_asset_sha256, page_asset_byte_size,
                       page_asset_parameters_json, page_asset_version
                FROM pages
                ORDER BY project_id, page_index
                """
            ).fetchall()
    except (OSError, sqlite3.Error, ValueError) as exc:
        findings.append(
            AuditFinding(
                "database_unreadable",
                "fatal",
                f"Could not read or audit the project database: {exc}",
                str(database),
            )
        )
        return _report(root, database, False, "unavailable", 0, 0, 0, findings)

    if not projects:
        findings.append(
            AuditFinding(
                "project_missing",
                "warning",
                "The database contains no project records.",
                str(database),
            )
        )
    elif len(projects) > 1:
        findings.append(
            AuditFinding(
                "multiple_projects",
                "warning",
                "This store contains multiple projects; processing currently expects one project per store.",
                str(database),
            )
        )

    for project in projects:
        source_path = Path(str(project["source_uri"])).expanduser()
        project_id = int(project["id"])
        title = str(project["title"])
        if not source_path.is_file():
            findings.append(
                AuditFinding(
                    "source_missing",
                    "warning",
                    f"Source file for project {project_id} ({title}) is missing at its recorded path.",
                    str(source_path),
                )
            )
            continue
        try:
            actual_hash = _file_sha256(source_path)
        except OSError as exc:
            findings.append(
                AuditFinding(
                    "source_unreadable",
                    "warning",
                    f"Could not read source file for project {project_id}: {exc}",
                    str(source_path),
                )
            )
            continue
        if actual_hash != str(project["source_sha256"]):
            findings.append(
                AuditFinding(
                    "source_hash_mismatch",
                    "warning",
                    f"Source content for project {project_id} differs from its recorded SHA-256.",
                    str(source_path),
                )
            )

    for page in pages:
        project_id = int(page["project_id"])
        page_index = int(page["page_index"])
        page_path = Path(str(page["source_image"])).expanduser()
        page_label = f"project {project_id} page {page_index + 1}"

        if not page_path.is_file():
            findings.append(
                AuditFinding(
                    "page_asset_missing",
                    "error",
                    (
                        f"Source image for {page_label} is missing."
                    ),
                    str(page_path),
                )
            )
            continue

        evidence = None
        if page["page_asset_sha256"] is not None:
            try:
                parameters = (
                    json.loads(page["page_asset_parameters_json"])
                    if page["page_asset_parameters_json"]
                    else {}
                )
            except json.JSONDecodeError:
                parameters = {}
            evidence = PageAssetEvidence(
                sha256=str(page["page_asset_sha256"]),
                byte_size=int(page["page_asset_byte_size"])
                if page["page_asset_byte_size"] is not None
                else 0,
                render_parameters=parameters,
                version=int(page["page_asset_version"])
                if page["page_asset_version"] is not None
                else 1,
            )
            if evidence.byte_size <= 0:
                findings.append(
                    AuditFinding(
                        "page_asset_evidence_invalid",
                        "error",
                        f"{page_label}: recorded page asset evidence has no usable size.",
                        str(page_path),
                    )
                )
                continue

        try:
            actual = _file_sha256(page_path)
        except OSError as exc:
            findings.append(
                AuditFinding(
                    "page_asset_unreadable",
                    "error",
                    f"{page_label}: raster is unreadable: {exc.strerror or exc}",
                    str(page_path),
                )
            )
            continue

        if evidence is not None and actual != evidence.sha256:
            findings.append(
                AuditFinding(
                    "page_asset_hash_mismatch",
                    "error",
                    (
                        f"{page_label}: rendered page image differs from its recorded "
                        f"SHA-256 (recorded {evidence.sha256[:12]}…, "
                        f"found {actual[:12]}…). Do not use it as evidence."
                    ),
                    str(page_path),
                )
            )
        elif evidence is None:
            findings.append(
                AuditFinding(
                    "page_asset_evidence_missing",
                    "warning",
                    (
                        f"{page_label}: imported before schema v11, so no page asset "
                        "digest was recorded. Run `scribeforge rehash-pages` to fill "
                        "the gap."
                    ),
                    str(page_path),
                )
            )

    return _report(
        root,
        database,
        True,
        integrity,
        foreign_key_violations,
        len(projects),
        len(pages),
        findings,
    )


def _report(
    root: Path,
    database: Path,
    database_readable: bool,
    sqlite_integrity: str,
    foreign_key_violations: int,
    project_count: int,
    page_count: int,
    findings: list[AuditFinding],
) -> ProjectAuditReport:
    return ProjectAuditReport(
        store_path=str(root),
        database_path=str(database),
        database_readable=database_readable,
        sqlite_integrity=sqlite_integrity,
        foreign_key_violations=foreign_key_violations,
        project_count=project_count,
        page_count=page_count,
        findings=tuple(findings),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
