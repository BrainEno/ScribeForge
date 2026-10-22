from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from xml.sax.saxutils import escape, quoteattr

from scribeforge.adapters.export_common import (
    XML_DECL,
    format_modified,
    require_xml_text,
    validate_identifier,
    validate_language,
    write_atomic,
    write_zip_entry,
)
from scribeforge.domain.candidate import OcrCandidateBook, OcrCandidatePage

_MIMETYPE = "application/epub+zip"
_OPF_NS = "http://www.idpf.org/2007/opf"
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_EPUB_NS = "http://www.idpf.org/2007/ops"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_CANDIDATE_WARNING = "UNVERIFIED OCR CANDIDATE — not accepted or proofread"


def _page_name(page_index: int) -> str:
    return f"page-{page_index + 1:06d}.xhtml"


def _container_xml() -> str:
    return (
        XML_DECL
        + "\n"
        + f'<container version="1.0" xmlns="{_CONTAINER_NS}">'
        + "<rootfiles>"
        + '<rootfile full-path="OEBPS/content.opf" '
        + 'media-type="application/oebps-package+xml"/>'
        + "</rootfiles></container>\n"
    )


def _candidate_title(book: OcrCandidateBook) -> str:
    return f"{book.source_title} — {_CANDIDATE_WARNING} ({book.engine})"


def _package_xml(
    book: OcrCandidateBook,
    *,
    identifier: str,
    language: str,
    modified: str,
) -> str:
    items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    ]
    spine: list[str] = []
    for page in book.pages:
        page_id = f"page-{page.page_index}"
        items.append(
            f'<item id="{page_id}" href="{_page_name(page.page_index)}" '
            'media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{page_id}"/>')
    versions = ", ".join(book.engine_versions) or "unknown"
    description = (
        f"{_CANDIDATE_WARNING}. Engine: {book.engine}; "
        f"persisted engine version(s): {versions}. "
        "Generated for side-by-side review; not a verified transcription."
    )
    return (
        XML_DECL
        + "\n"
        + f'<package xmlns="{_OPF_NS}" version="3.0" unique-identifier="book-id">'
        + f'<metadata xmlns:dc="{_DC_NS}">'
        + f'<dc:identifier id="book-id">{escape(identifier)}</dc:identifier>'
        + f"<dc:title>{escape(_candidate_title(book))}</dc:title>"
        + f"<dc:language>{escape(language)}</dc:language>"
        + f"<dc:description>{escape(description)}</dc:description>"
        + f'<meta property="dcterms:modified">{modified}</meta>'
        + "</metadata>"
        + f"<manifest>{''.join(items)}</manifest>"
        + f"<spine>{''.join(spine)}</spine>"
        + "</package>\n"
    )


def _nav_xhtml(book: OcrCandidateBook, language: str) -> str:
    lang = quoteattr(language)
    entries = "".join(
        f'<li><a href="{_page_name(page.page_index)}">Source page {page.page_index + 1}</a></li>'
        for page in book.pages
    )
    title = escape(_candidate_title(book))
    return (
        XML_DECL
        + "\n<!DOCTYPE html>\n"
        + f'<html xmlns="{_XHTML_NS}" xmlns:epub="{_EPUB_NS}" lang={lang} xml:lang={lang}>'
        + f"<head><title>{title}</title></head>"
        + f'<body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{entries}</ol></nav></body>'
        + "</html>\n"
    )


def _page_xhtml(page: OcrCandidatePage, language: str) -> str:
    lang = quoteattr(language)
    paragraphs = "".join(f"<p>{escape(line)}</p>" for line in page.lines)
    index = page.page_index + 1
    return (
        XML_DECL
        + "\n<!DOCTYPE html>\n"
        + f'<html xmlns="{_XHTML_NS}" lang={lang} xml:lang={lang}>'
        + f"<head><title>Source page {index}</title></head>"
        + f"<body><section>{paragraphs}</section></body>"
        + "</html>\n"
    )


def build_candidate_epub(
    book: OcrCandidateBook,
    *,
    identifier: str,
    language: str,
    modified: datetime,
) -> bytes:
    """Build a source-page-aligned EPUB from raw engine output, clearly marked unverified."""
    if not book.pages:
        raise ValueError("cannot export an OCR candidate with no pages")
    if not book.engine.strip():
        raise ValueError("OCR candidate engine name is required")
    page_indexes = [page.page_index for page in book.pages]
    if len(set(page_indexes)) != len(page_indexes) or any(index < 0 for index in page_indexes):
        raise ValueError("OCR candidate pages must have unique non-negative indexes")
    if page_indexes != sorted(page_indexes):
        raise ValueError("OCR candidate pages must be ordered by source page index")
    validate_identifier(identifier)
    validate_language(language)
    require_xml_text(book.source_title, context="source title")
    require_xml_text(book.engine, context="engine name")
    require_xml_text(_candidate_title(book), context="candidate title")
    for page in book.pages:
        for line_index, line in enumerate(page.lines):
            require_xml_text(line, context=f"page {page.page_index} line {line_index}")
    modified_text = format_modified(modified)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        write_zip_entry(archive, "mimetype", _MIMETYPE, zipfile.ZIP_STORED)
        write_zip_entry(
            archive, "META-INF/container.xml", _container_xml(), zipfile.ZIP_DEFLATED
        )
        write_zip_entry(
            archive,
            "OEBPS/content.opf",
            _package_xml(
                book,
                identifier=identifier,
                language=language,
                modified=modified_text,
            ),
            zipfile.ZIP_DEFLATED,
        )
        write_zip_entry(
            archive, "OEBPS/nav.xhtml", _nav_xhtml(book, language), zipfile.ZIP_DEFLATED
        )
        for page in book.pages:
            write_zip_entry(
                archive,
                f"OEBPS/{_page_name(page.page_index)}",
                _page_xhtml(page, language),
                zipfile.ZIP_DEFLATED,
            )
    data = buffer.getvalue()
    _check_candidate_epub(data, book)
    return data


def write_candidate_epub(
    book: OcrCandidateBook,
    path: Path,
    *,
    identifier: str,
    language: str,
    modified: datetime,
) -> Path:
    """Write an unverified candidate EPUB atomically."""
    return write_atomic(
        path,
        build_candidate_epub(
            book,
            identifier=identifier,
            language=language,
            modified=modified,
        ),
    )


def _check_candidate_epub(data: bytes, book: OcrCandidateBook) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.testzip() is not None:
                raise ValueError("candidate EPUB archive checksum failed")
            names = set(archive.namelist())
            if "mimetype" not in names or archive.read("mimetype") != _MIMETYPE.encode("ascii"):
                raise ValueError("candidate EPUB has an invalid mimetype entry")
            for name in ("META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml"):
                ElementTree.fromstring(archive.read(name))
            for page in book.pages:
                name = f"OEBPS/{_page_name(page.page_index)}"
                root = ElementTree.fromstring(archive.read(name))
                actual = tuple(
                    element.text or ""
                    for element in root.iter(f"{{{_XHTML_NS}}}p")
                )
                if actual != page.lines:
                    raise ValueError(
                        f"candidate EPUB integrity mismatch on source page {page.page_index}"
                    )
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError(f"candidate EPUB failed integrity check: {exc}") from exc
