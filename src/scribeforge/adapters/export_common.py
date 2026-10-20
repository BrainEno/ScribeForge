from __future__ import annotations

import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
# Fixed DOS epoch so two exports with the same inputs produce identical bytes.
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$")


def format_modified(modified: datetime) -> str:
    """Render a timezone-aware timestamp as a W3CDTF UTC string."""

    if modified.tzinfo is None:
        raise ValueError("modified time must be timezone-aware")
    return modified.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_language(language: str) -> str:
    """Reject language tags that could break out of an XML attribute."""

    if not _LANGUAGE_TAG.match(language):
        raise ValueError(f"invalid language tag: {language!r}")
    return language


def validate_identifier(identifier: str) -> str:
    if not identifier.strip():
        raise ValueError("export identifier must not be empty")
    return identifier


def write_zip_entry(
    archive: zipfile.ZipFile, name: str, text: str, compression: int
) -> None:
    """Write one UTF-8 entry with a fixed timestamp for reproducible archives."""

    info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    info.compress_type = compression
    info.external_attr = 0o644 << 16
    archive.writestr(info, text.encode("utf-8"))


def write_atomic(path: Path, data: bytes) -> Path:
    """Replace the destination atomically so an interrupted write leaves no torn file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
