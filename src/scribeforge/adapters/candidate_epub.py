"""Candidate EPUB export: the trusted builder's unverified downgrade mode.

Raw OCR output for side-by-side A/B comparison is rendered by the single shared
package builder in :mod:`scribeforge.adapters.epub_builder` with
``unverified=True``, which fails closed unless the UNVERIFIED warning is in the
metadata. This module only maps an :class:`OcrCandidateBook` onto content
documents and re-checks that every exported line round-trips (#72).
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

from scribeforge.adapters import epub_builder
from scribeforge.adapters.epub_builder import EpubDocument
from scribeforge.adapters.export_common import (
    XML_DECL,
    format_modified,
    require_xml_text,
    validate_identifier,
    validate_language,
    write_atomic,
)
from scribeforge.domain.candidate import OcrCandidateBook, OcrCandidatePage

_XHTML_NS = "http://www.w3.org/1999/xhtml"
_CANDIDATE_WARNING = epub_builder.UNVERIFIED_CANDIDATE_WARNING


def _page_name(page_index: int) -> str:
    return f"page-{page_index + 1:06d}.xhtml"


def _candidate_title(book: OcrCandidateBook) -> str:
    return f"{book.source_title} — {_CANDIDATE_WARNING} ({book.engine})"


def _candidate_description(book: OcrCandidateBook) -> str:
    versions = ", ".join(book.engine_versions) or "unknown"
    return (
        f"{_CANDIDATE_WARNING}. Engine: {book.engine}; "
        f"persisted engine version(s): {versions}. "
        "Generated for side-by-side review; not a verified transcription."
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

    documents = [
        EpubDocument(
            item_id=f"page-{page.page_index}",
            file_name=_page_name(page.page_index),
            xhtml=_page_xhtml(page, language),
            nav_label=f"Source page {page.page_index + 1}",
            nav_level=1,
        )
        for page in book.pages
    ]
    data = epub_builder.build_epub_package(
        title=_candidate_title(book),
        identifier=identifier,
        language=language,
        modified=format_modified(modified),
        description=_candidate_description(book),
        documents=documents,
        nav_title=_candidate_title(book),
        unverified=True,
    )
    _verify_candidate_pages(data, book)
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


def _verify_candidate_pages(data: bytes, book: OcrCandidateBook) -> None:
    """Every exported page must still carry exactly the raw engine lines."""

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for page in book.pages:
                name = f"OEBPS/{_page_name(page.page_index)}"
                root = ElementTree.fromstring(archive.read(name))
                actual = tuple(element.text or "" for element in root.iter(f"{{{_XHTML_NS}}}p"))
                if actual != page.lines:
                    raise ValueError(
                        f"candidate EPUB integrity mismatch on source page {page.page_index}"
                    )
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError(f"candidate EPUB failed integrity check: {exc}") from exc
