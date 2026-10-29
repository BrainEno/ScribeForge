from __future__ import annotations

import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from scribeforge.domain.content import VerifiedBook, VerifiedImage

XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
#: Directory inside an EPUB package that holds the page rasters. Both the writer
#: and the integrity check read the convention from here, so they cannot drift.
IMAGES_DIR = "images"
# Fixed DOS epoch so two exports with the same inputs produce identical bytes.
ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$")
# XML 1.0 forbids most C0 controls and lone surrogates. Writing them would emit an
# archive that no reader can parse, so the exporters reject them instead of
# silently dropping characters from the source text.
_XML_INVALID = re.compile(
    r"[^\t\n\r\u0020-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]"
)


def epub_image_name(image: VerifiedImage) -> str:
    """Archive path of one page raster, relative to the package root."""

    return f"OEBPS/{IMAGES_DIR}/{image.file_name}"


def format_modified(modified: datetime) -> str:
    """Render a timezone-aware timestamp as a W3CDTF timezone.utc string."""

    if modified.tzinfo is None:
        raise ValueError("modified time must be timezone-aware")
    return modified.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_language(language: str) -> str:
    """Reject language tags that could break out of an XML attribute."""

    if not _LANGUAGE_TAG.match(language):
        raise ValueError(f"invalid language tag: {language!r}")
    return language


def require_xml_text(value: str, *, context: str) -> str:
    """Reject characters XML 1.0 cannot represent instead of corrupting the export."""

    match = _XML_INVALID.search(value)
    if match is not None:
        raise ValueError(
            f"{context} contains a character XML cannot represent: "
            f"U+{ord(match.group()):04X}"
        )
    return value


def validate_identifier(identifier: str) -> str:
    if not identifier.strip():
        raise ValueError("export identifier must not be empty")
    return require_xml_text(identifier, context="identifier")


def validate_book_text(book: VerifiedBook, *, author: str | None) -> None:
    """Check every string the export will encode, naming the offending source line."""

    require_xml_text(book.title, context="book title")
    for image in book.images:
        require_xml_text(image.alt, context=f"page {image.page_index} image caption")
    if author is not None:
        require_xml_text(author, context="author")
    for page in book.pages:
        for line_index, line in enumerate(page.lines):
            require_xml_text(line, context=f"page {page.page_index} line {line_index}")


def write_zip_entry(
    archive: zipfile.ZipFile, name: str, text: str, compression: int
) -> None:
    """Write one UTF-8 entry with a fixed timestamp for reproducible archives."""

    write_zip_bytes(archive, name, text.encode("utf-8"), compression)


def write_zip_bytes(
    archive: zipfile.ZipFile, name: str, data: bytes, compression: int
) -> None:
    """Write one binary entry with the same fixed timestamp as text entries.

    Page rasters go through here: an image must be byte-identical to the evidence
    it came from, so nothing about it may be re-encoded on the way in.
    """

    info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    info.compress_type = compression
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


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
