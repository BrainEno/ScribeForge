from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from scribeforge.adapters.export_common import (
    XML_DECL,
    format_modified,
    validate_identifier,
    validate_language,
    write_atomic,
    write_zip_entry,
)
from scribeforge.domain.content import VerifiedBook

_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CORE_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_DCTERMS_NS = "http://purl.org/dc/terms/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

_OFFICE_DOCUMENT_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_CORE_PROPERTIES_REL = (
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
)
_DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_CORE_PROPERTIES_CONTENT_TYPE = (
    "application/vnd.openxmlformats-package.core-properties+xml"
)
_RELATIONSHIPS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-package.relationships+xml"
)


def _content_types_xml() -> str:
    return (
        XML_DECL
        + "\n"
        + f'<Types xmlns="{_CONTENT_TYPES_NS}">'
        + f'<Default Extension="rels" ContentType="{_RELATIONSHIPS_CONTENT_TYPE}"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + f'<Override PartName="/word/document.xml" ContentType="{_DOCUMENT_CONTENT_TYPE}"/>'
        + '<Override PartName="/docProps/core.xml" '
        + f'ContentType="{_CORE_PROPERTIES_CONTENT_TYPE}"/>'
        + "</Types>\n"
    )


def _package_relationships_xml() -> str:
    return (
        XML_DECL
        + "\n"
        + f'<Relationships xmlns="{_RELATIONSHIPS_NS}">'
        + f'<Relationship Id="rId1" Type="{_OFFICE_DOCUMENT_REL}" '
        + 'Target="word/document.xml"/>'
        + f'<Relationship Id="rId2" Type="{_CORE_PROPERTIES_REL}" '
        + 'Target="docProps/core.xml"/>'
        + "</Relationships>\n"
    )


def _core_properties_xml(
    book: VerifiedBook,
    *,
    identifier: str,
    language: str,
    modified: str,
) -> str:
    return (
        XML_DECL
        + "\n"
        + f'<cp:coreProperties xmlns:cp="{_CORE_NS}" xmlns:dc="{_DC_NS}" '
        + f'xmlns:dcterms="{_DCTERMS_NS}" xmlns:xsi="{_XSI_NS}">'
        + f"<dc:title>{escape(book.title)}</dc:title>"
        + f"<dc:identifier>{escape(identifier)}</dc:identifier>"
        + f"<dc:language>{escape(language)}</dc:language>"
        + f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified>'
        + "</cp:coreProperties>\n"
    )


def _paragraph_xml(text: str) -> str:
    if text == "":
        return "<w:p/>"
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _document_xml(book: VerifiedBook) -> str:
    paragraphs: list[str] = []
    for index, page in enumerate(book.pages):
        if index > 0:
            paragraphs.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        paragraphs.extend(_paragraph_xml(line) for line in page.lines)
    return (
        XML_DECL
        + "\n"
        + f'<w:document xmlns:w="{_WORD_NS}"><w:body>'
        + "".join(paragraphs)
        + "<w:sectPr/></w:body></w:document>\n"
    )


def build_docx(
    book: VerifiedBook,
    *,
    identifier: str,
    language: str,
    modified: datetime,
) -> bytes:
    """Render a verified book as a deterministic WordprocessingML document."""

    if not book.pages:
        raise ValueError("cannot export a book with no pages")
    validate_identifier(identifier)
    validate_language(language)
    modified_text = format_modified(modified)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        write_zip_entry(
            archive, "[Content_Types].xml", _content_types_xml(), zipfile.ZIP_DEFLATED
        )
        write_zip_entry(
            archive, "_rels/.rels", _package_relationships_xml(), zipfile.ZIP_DEFLATED
        )
        write_zip_entry(
            archive, "word/document.xml", _document_xml(book), zipfile.ZIP_DEFLATED
        )
        write_zip_entry(
            archive,
            "docProps/core.xml",
            _core_properties_xml(
                book, identifier=identifier, language=language, modified=modified_text
            ),
            zipfile.ZIP_DEFLATED,
        )
    return buffer.getvalue()


def write_docx(
    book: VerifiedBook,
    path: Path,
    *,
    identifier: str,
    language: str,
    modified: datetime,
) -> Path:
    """Write a DOCX atomically so an interrupted export never leaves a torn file."""

    return write_atomic(
        path,
        build_docx(book, identifier=identifier, language=language, modified=modified),
    )
