from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from scribeforge.project_audit import audit_project_store

_FORMAT = "scribeforge-project-backup"
_SCHEMA_VERSION = 1
_MANIFEST_NAME = "manifest.json"
_MAX_ARCHIVE_FILES = 200_000
_MAX_UNCOMPRESSED_BYTES = 200 * 1024**3
_MAX_MANIFEST_BYTES = 16 * 1024**2
_CHUNK_SIZE = 1024 * 1024


class ProjectBackupError(ValueError):
    """The project or archive cannot be backed up/restored safely."""


def create_project_backup(
    store_path: Path, backup_path: Path, *, overwrite: bool = False
) -> dict[str, Any]:
    """Create a hashed archive of a project store with a consistent SQLite snapshot."""
    root = store_path.expanduser().resolve()
    if not root.is_dir():
        raise ProjectBackupError(f"project store directory not found: {root}")
    database = root / "project.sqlite3"
    if not database.is_file():
        raise ProjectBackupError(f"project database not found: {database}")

    destination = backup_path.expanduser().resolve()
    if destination == root or destination.is_relative_to(root):
        raise ProjectBackupError("backup destination must be outside the project store")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"backup already exists: {destination}")

    audit = audit_project_store(root)
    blocking = [finding for finding in audit.findings if finding.severity in {"fatal", "error"}]
    if blocking:
        detail = "; ".join(f"{item.code}: {item.message}" for item in blocking)
        raise ProjectBackupError(f"project audit found integrity errors; backup refused: {detail}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scribeforge-backup-") as temporary_root:
        snapshot = Path(temporary_root) / "project.sqlite3"
        _snapshot_database(database, snapshot)
        file_map: dict[str, Path] = {"project.sqlite3": snapshot}
        for candidate in sorted(root.rglob("*")):
            if candidate.is_symlink():
                raise ProjectBackupError(f"project store contains a symlink; backup refused: {candidate}")
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            if relative == "project.sqlite3":
                continue
            if relative in {"project.sqlite3-wal", "project.sqlite3-shm"}:
                continue
            if relative == _MANIFEST_NAME:
                raise ProjectBackupError(
                    f"project store contains reserved backup filename {_MANIFEST_NAME!r}"
                )
            file_map[relative] = candidate

        sources = _prepare_source_files(snapshot, root, file_map)
        files: list[dict[str, Any]] = []
        for relative, source_file in sorted(file_map.items()):
            size, digest = _file_fingerprint(source_file)
            files.append({"path": relative, "size": size, "sha256": digest})

        manifest = {
            "format": _FORMAT,
            "schema_version": _SCHEMA_VERSION,
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_store": str(root),
            "files": files,
            "sources": sources,
            "audit_warnings": [
                finding.to_dict() for finding in audit.findings if finding.severity == "warning"
            ],
        }

        temporary_archive = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            with zipfile.ZipFile(
                temporary_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                for record in files:
                    relative = str(record["path"])
                    _stream_file_to_zip(
                        archive,
                        relative,
                        file_map[relative],
                        expected_size=int(record["size"]),
                        expected_sha256=str(record["sha256"]),
                    )
                archive.writestr(
                    _MANIFEST_NAME,
                    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                )
            _verify_archive(temporary_archive)
            if destination.exists() and not overwrite:
                raise FileExistsError(f"backup already exists: {destination}")
            temporary_archive.replace(destination)
        finally:
            temporary_archive.unlink(missing_ok=True)

    return {
        "archive": str(destination),
        "source_store": str(root),
        "file_count": len(files),
        "total_bytes": sum(int(item["size"]) for item in files),
        "created_at": manifest["created_at"],
        "healthy": True,
    }


def verify_project_backup(backup_path: Path) -> dict[str, Any]:
    """Validate an archive's member list, path safety, hashes and SQLite snapshot."""
    archive_path = backup_path.expanduser().resolve()
    manifest, files, total_bytes, integrity, foreign_key_violations = _verify_archive(archive_path)
    return {
        "archive": str(archive_path),
        "format": _FORMAT,
        "schema_version": _SCHEMA_VERSION,
        "source_store": manifest["source_store"],
        "created_at": manifest["created_at"],
        "file_count": len(files),
        "total_bytes": total_bytes,
        "sqlite_integrity": integrity,
        "foreign_key_violations": foreign_key_violations,
        "audit_warnings": manifest.get("audit_warnings", []),
        "healthy": True,
    }


def restore_project_backup(backup_path: Path, target_path: Path) -> dict[str, Any]:
    """Validate then restore into a new directory; never overwrite an existing directory."""
    archive_path = backup_path.expanduser().resolve()
    target = target_path.expanduser().resolve()
    if target.exists():
        raise FileExistsError(f"restore destination already exists: {target}")
    if target == archive_path or target.is_relative_to(archive_path):
        raise ProjectBackupError("restore destination must be a directory separate from the backup file")

    manifest, files, total_bytes, _integrity, _fk_violations = _verify_archive(archive_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=target.parent))
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for record in files:
                relative = str(record["path"])
                output = stage.joinpath(*PurePosixPath(relative).parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with archive.open(relative, "r") as source, output.open("wb") as destination:
                    for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
                        destination.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                if written != int(record["size"]) or digest.hexdigest() != record["sha256"]:
                    raise ProjectBackupError(f"archive member changed while restoring: {relative}")

        database = stage / "project.sqlite3"
        archived_names = {str(record["path"]) for record in files}
        _remap_page_assets(database, stage, archived_names)
        _remap_project_sources(database, stage, manifest["sources"])
        audit = audit_project_store(stage)
        blocking = [
            finding for finding in audit.findings if finding.severity in {"fatal", "error"}
        ]
        if blocking:
            detail = "; ".join(f"{item.code}: {item.message}" for item in blocking)
            raise ProjectBackupError(f"restored project failed integrity checks: {detail}")

        # Store paths in SQLite must refer to the final target, not the staging directory.
        _remap_page_assets(database, target, archived_names)
        _remap_project_sources(database, target, manifest["sources"])
        _validate_sqlite_database(database)
        stage.rename(target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    restored_audit = audit_project_store(target)
    return {
        "backup": str(archive_path),
        "store": str(target),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "project_count": restored_audit.project_count,
        "page_count": restored_audit.page_count,
        "healthy": not any(
            finding.severity in {"fatal", "error"} for finding in restored_audit.findings
        ),
        "audit_warnings": [finding.to_dict() for finding in restored_audit.findings],
        "source_store": manifest["source_store"],
    }


def _snapshot_database(source_path: Path, destination_path: Path) -> None:
    try:
        with closing(sqlite3.connect(source_path, timeout=10)) as source:
            table_exists = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            if table_exists is not None:
                running = int(
                    source.execute("SELECT COUNT(*) FROM jobs WHERE state = 'running'").fetchone()[0]
                )
                if running:
                    raise ProjectBackupError(
                        f"project has {running} running job(s); wait for processing to finish before backup"
                    )
            with closing(sqlite3.connect(destination_path)) as destination:
                source.backup(destination)
    except sqlite3.Error as exc:
        raise ProjectBackupError(f"could not create a consistent SQLite snapshot: {exc}") from exc
    _validate_sqlite_database(destination_path)


def _verify_archive(
    archive_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], int, str, int]:
    if not archive_path.is_file():
        raise ProjectBackupError(f"backup archive not found: {archive_path}")

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ARCHIVE_FILES + 1:
                raise ProjectBackupError("backup archive contains too many files")
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                raise ProjectBackupError("backup archive contains duplicate paths")
            for item in infos:
                if item.is_dir():
                    raise ProjectBackupError("backup archive must not contain directory entries")
                _validate_archive_path(item.filename)
                mode = (item.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    raise ProjectBackupError(f"backup archive contains a symlink: {item.filename}")
            if _MANIFEST_NAME not in names:
                raise ProjectBackupError("backup manifest is missing")
            manifest_info = next(item for item in infos if item.filename == _MANIFEST_NAME)
            if manifest_info.file_size > _MAX_MANIFEST_BYTES:
                raise ProjectBackupError("backup manifest exceeds the maximum allowed size")
            try:
                manifest = json.loads(archive.read(_MANIFEST_NAME))
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as exc:
                raise ProjectBackupError(f"backup manifest is invalid: {exc}") from exc

            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != _FORMAT
                or manifest.get("schema_version") != _SCHEMA_VERSION
                or not isinstance(manifest.get("source_store"), str)
                or not isinstance(manifest.get("created_at"), str)
                or not isinstance(manifest.get("files"), list)
                or not isinstance(manifest.get("sources"), list)
            ):
                raise ProjectBackupError("unsupported or invalid ScribeForge backup manifest")

            files = manifest["files"]
            if not files or len(files) > _MAX_ARCHIVE_FILES:
                raise ProjectBackupError("backup manifest contains an invalid file count")
            file_names: list[str] = []
            total_bytes = 0
            for record in files:
                if not isinstance(record, dict):
                    raise ProjectBackupError("backup manifest contains an invalid file record")
                relative = record.get("path")
                size = record.get("size")
                digest = record.get("sha256")
                if not isinstance(relative, str):
                    raise ProjectBackupError("backup manifest contains a non-string file path")
                _validate_archive_path(relative)
                if relative == _MANIFEST_NAME:
                    raise ProjectBackupError("backup manifest cannot list itself as a project file")
                if not isinstance(size, int) or size < 0:
                    raise ProjectBackupError(f"invalid file size for {relative}")
                if (
                    not isinstance(digest, str)
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)
                ):
                    raise ProjectBackupError(f"invalid SHA-256 for {relative}")
                file_names.append(relative)
                total_bytes += size
                if total_bytes > _MAX_UNCOMPRESSED_BYTES:
                    raise ProjectBackupError("backup archive exceeds the maximum uncompressed size")

            if len(file_names) != len(set(file_names)):
                raise ProjectBackupError("backup manifest contains duplicate file paths")
            if set(names) != set(file_names) | {_MANIFEST_NAME}:
                raise ProjectBackupError("archive members do not match the backup manifest")
            if "project.sqlite3" not in file_names:
                raise ProjectBackupError("backup does not contain project.sqlite3")

            records_by_name = {str(record["path"]): record for record in files}
            source_projects: set[int] = set()
            for source in manifest["sources"]:
                if not isinstance(source, dict):
                    raise ProjectBackupError("backup manifest contains an invalid source record")
                project_id = source.get("project_id")
                source_member = source.get("archive_path")
                original_path = source.get("original_path")
                source_sha256 = source.get("sha256")
                if (
                    not isinstance(project_id, int)
                    or project_id < 1
                    or project_id in source_projects
                    or not isinstance(original_path, str)
                    or not original_path
                    or not isinstance(source_member, str)
                    or not isinstance(source_sha256, str)
                ):
                    raise ProjectBackupError("backup manifest contains an invalid source record")
                _validate_archive_path(source_member)
                record = records_by_name.get(source_member)
                if record is None or record["sha256"] != source_sha256:
                    raise ProjectBackupError(
                        f"backup source record does not match archived bytes: {source_member}"
                    )
                source_projects.add(project_id)

            info_by_name = {item.filename: item for item in infos}
            for record in files:
                relative = str(record["path"])
                info = info_by_name[relative]
                expected_size = int(record["size"])
                if info.file_size != expected_size:
                    raise ProjectBackupError(f"size mismatch for archive member {relative}")
                digest = hashlib.sha256()
                read_bytes = 0
                with archive.open(relative, "r") as member:
                    for chunk in iter(lambda: member.read(_CHUNK_SIZE), b""):
                        digest.update(chunk)
                        read_bytes += len(chunk)
                if read_bytes != expected_size or digest.hexdigest() != record["sha256"]:
                    raise ProjectBackupError(f"SHA-256 mismatch for archive member {relative}")

            with tempfile.TemporaryDirectory(prefix="scribeforge-verify-") as temporary_root:
                database = Path(temporary_root) / "project.sqlite3"
                with archive.open("project.sqlite3", "r") as source, database.open("wb") as target:
                    shutil.copyfileobj(source, target, length=_CHUNK_SIZE)
                integrity, violations = _validate_sqlite_database(database)
                _validate_page_asset_references(database, set(file_names))
            if violations:
                raise ProjectBackupError(
                    f"backup database has {violations} foreign-key violation(s)"
                )
            return manifest, files, total_bytes, integrity, violations
    except (OSError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        if isinstance(exc, ProjectBackupError):
            raise
        raise ProjectBackupError(f"could not verify backup archive: {exc}") from exc


def _validate_archive_path(value: str) -> None:
    if (
        not value
        or "\\" in value
        or value.startswith(("/", "\\"))
        or ":" in value.split("/", maxsplit=1)[0]
    ):
        raise ProjectBackupError(f"unsafe archive path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ProjectBackupError(f"unsafe archive path: {value!r}")
    if path.as_posix() != value:
        raise ProjectBackupError(f"unsafe archive path: {value!r}")


def _remap_page_assets(database: Path, destination_root: Path, archive_names: Iterable[str]) -> None:
    internal_assets = [
        name for name in archive_names if name.startswith("assets/") and len(PurePosixPath(name).parts) > 1
    ]
    if not internal_assets:
        return
    try:
        with sqlite3.connect(database) as connection:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'"
            ).fetchone()
            if table_exists is None:
                raise ProjectBackupError("backup database has no pages table")
            rows = connection.execute("SELECT id, source_image FROM pages").fetchall()
            for row_id, original in rows:
                normalized = str(original).replace("\\", "/").lstrip("./")
                matches = [
                    name
                    for name in internal_assets
                    if normalized == name or normalized.endswith("/" + name)
                ]
                if len(matches) == 1:
                    restored_path = str((destination_root / PurePosixPath(matches[0])).resolve())
                    connection.execute(
                        "UPDATE pages SET source_image = ? WHERE id = ?",
                        (restored_path, int(row_id)),
                    )
    except sqlite3.Error as exc:
        raise ProjectBackupError(f"could not relocate restored page assets: {exc}") from exc


def _validate_sqlite_database(path: Path) -> tuple[str, int]:
    try:
        database_uri = f"{path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(database_uri, uri=True)) as connection:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            integrity = "; ".join(str(row[0]) for row in integrity_rows)
            if integrity_rows != [("ok",)] and not (
                len(integrity_rows) == 1 and str(integrity_rows[0][0]) == "ok"
            ):
                raise ProjectBackupError(f"SQLite integrity check failed: {integrity}")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not {"projects", "pages"}.issubset(tables):
                raise ProjectBackupError("SQLite database is not a ScribeForge project store")
            violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    except sqlite3.Error as exc:
        raise ProjectBackupError(f"could not validate SQLite database: {exc}") from exc
    return integrity, violations


def _file_fingerprint(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _stream_file_to_zip(
    archive: zipfile.ZipFile,
    member_name: str,
    source_path: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    info = zipfile.ZipInfo(member_name)
    info.compress_type = zipfile.ZIP_DEFLATED
    digest = hashlib.sha256()
    size = 0
    with source_path.open("rb") as source, archive.open(info, "w") as destination:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
            destination.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise ProjectBackupError(f"project file changed while backing up: {source_path}")



def _prepare_source_files(
    snapshot_path: Path, store_root: Path, file_map: dict[str, Path]
) -> list[dict[str, Any]]:
    """Include each original source document after checking its recorded identity."""
    try:
        with closing(sqlite3.connect(snapshot_path)) as connection:
            projects = connection.execute(
                "SELECT id, source_uri, source_sha256 FROM projects ORDER BY id"
            ).fetchall()
    except sqlite3.Error as exc:
        raise ProjectBackupError(f"could not inspect project sources: {exc}") from exc
    if not projects:
        raise ProjectBackupError("project store contains no project to back up")

    source_records: list[dict[str, Any]] = []
    reserved: dict[str, str] = {}
    for project_id_raw, source_uri_raw, expected_hash_raw in projects:
        project_id = int(project_id_raw)
        source_path = Path(str(source_uri_raw)).expanduser().resolve()
        if not source_path.is_file():
            raise ProjectBackupError(
                f"source file for project {project_id} is missing: {source_path}; "
                "restore the original file or use relink-source before backing up"
            )
        actual_size, actual_hash = _file_fingerprint(source_path)
        expected_hash = str(expected_hash_raw)
        if actual_hash != expected_hash:
            raise ProjectBackupError(
                f"source SHA-256 mismatch for project {project_id}: {source_path}; "
                "use the original source or relink a byte-identical file before backing up"
            )

        try:
            relative_to_store = source_path.relative_to(store_root).as_posix()
        except ValueError:
            relative_to_store = ""
        if relative_to_store and relative_to_store in file_map:
            archive_path = relative_to_store
        else:
            filename = (source_path.name or f"source-{project_id}.pdf").replace("\\", "_")
            archive_path = f"source/project-{project_id}/{filename}"
            existing = reserved.get(archive_path)
            if existing is not None and existing != str(source_path):
                raise ProjectBackupError(f"source archive path collision: {archive_path}")
            occupied = file_map.get(archive_path)
            if occupied is not None and occupied.resolve() != source_path:
                raise ProjectBackupError(f"source archive path collision: {archive_path}")
            reserved[archive_path] = str(source_path)
            file_map[archive_path] = source_path

        source_records.append(
            {
                "project_id": project_id,
                "original_path": str(source_path),
                "archive_path": archive_path,
                "sha256": expected_hash,
                "size": actual_size,
            }
        )
    return source_records


def _remap_project_sources(
    database: Path, destination_root: Path, source_records: list[dict[str, Any]]
) -> None:
    """Point restored projects at their archived source documents, preserving hashes."""
    try:
        with closing(sqlite3.connect(database)) as connection, connection:
            for source in source_records:
                member = str(source["archive_path"])
                target = str((destination_root / PurePosixPath(member)).resolve())
                cursor = connection.execute(
                    """
                    UPDATE projects
                    SET source_uri = ?
                    WHERE id = ? AND source_sha256 = ?
                    """,
                    (target, int(source["project_id"]), str(source["sha256"])),
                )
                if cursor.rowcount != 1:
                    raise ProjectBackupError(
                        "backup source record does not match a restored project: "
                        f"{source['project_id']}"
                    )
    except sqlite3.Error as exc:
        raise ProjectBackupError(f"could not relocate restored source files: {exc}") from exc


def _validate_page_asset_references(database: Path, archive_names: set[str]) -> None:
    """Require every persisted page raster to be represented in the archive."""
    assets = [
        name for name in archive_names if name.startswith("assets/") and len(PurePosixPath(name).parts) > 1
    ]
    try:
        with closing(sqlite3.connect(database)) as connection:
            rows = connection.execute("SELECT source_image FROM pages").fetchall()
    except sqlite3.Error as exc:
        raise ProjectBackupError(f"could not inspect archived page references: {exc}") from exc
    for (source_image,) in rows:
        normalized = str(source_image).replace("\\", "/").lstrip("./")
        matches = [
            name for name in assets
            if normalized == name or normalized.endswith("/" + name)
        ]
        if len(matches) != 1:
            raise ProjectBackupError(
                "backup is missing or ambiguously maps a page image referenced by the project: "
                f"{source_image}"
            )
