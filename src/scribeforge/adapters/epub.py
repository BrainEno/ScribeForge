from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from scribeforge.adapters.export_check import check_epub
from scribeforge.adapters.export_common import (
    XML_DECL,
    format_modified,
    validate_book_text,
    validate_identifier,
    validate_language,
    write_atomic,
    write_zip_entry,
)
from scribeforge.domain.content import VerifiedBook, VerifiedChapter, VerifiedPage

_MIMETYPE = "application/epub+zip"
_OPF_NS = "http://www.idpf.org/2007/opf"
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_EPUB_NS = "http://www.idpf.org/2007/ops"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_DC_NS = "http://purl.org/dc/elements/1.1/"


def _page_file_name(page_index: int) -> str:
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


def _package_xml(
    book: VerifiedBook,
    *,
    identifier: str,
    language: str,
    modified: str,
    author: str | None,
) -> str:
    items = [
        (
            '<item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>'
        )
    ]
    spine: list[str] = []
    for page in book.pages:
        items.append(
            f'<item id="page-{page.page_index}" href="{_page_file_name(page.page_index)}" '
            'media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="page-{page.page_index}"/>')
    creator = "" if author is None else f"<dc:creator>{escape(author)}</dc:creator>"
    return (
        XML_DECL
        + "\n"
        + f'<package xmlns="{_OPF_NS}" version="3.0" unique-identifier="book-id">'
        + f'<metadata xmlns:dc="{_DC_NS}">'
        + f'<dc:identifier id="book-id">{escape(identifier)}</dc:identifier>'
        + f"<dc:title>{escape(book.title)}</dc:title>"
        + creator
        + f"<dc:language>{escape(language)}</dc:language>"
        + f'<meta property="dcterms:modified">{modified}</meta>'
        + "</metadata>"
        + f"<manifest>{''.join(items)}</manifest>"
        + f"<spine>{''.join(spine)}</spine>"
        + "</package>\n"
    )


def _nav_entries(book: VerifiedBook) -> str:
    """Nested table of contents.

    Without a detected chapter tree a 562-page book exports as 562 flat entries
    called "Page 1".."Page 562", which is not a table of contents at all. When
    chapters exist the nav mirrors their levels; the per-page list is kept only as
    the fallback for a book whose structure has not been derived yet.
    """

    if not book.chapters:
        return "".join(
            f'<li><a href="{_page_file_name(page.page_index)}">'
            f"Page {page.page_index + 1}</a></li>"
            for page in book.pages
        )

    def link(chapter: VerifiedChapter) -> str:
        return (
            f'<li><a href="{_page_file_name(chapter.page_index)}">'
            f"{escape(chapter.title)}</a>"
        )

    # Emit nested lists with an explicit stack of open levels. Building the
    # closing tags by string arithmetic on levels is easy to get wrong, and the
    # first attempt produced a mismatched-tag XHTML that the export integrity
    # check caught — which is exactly why that check exists.
    parts: list[str] = []
    stack: list[int] = []
    for chapter in book.chapters:
        level = max(1, chapter.level)
        while stack and level < stack[-1]:
            parts.append("</li></ol>")
            stack.pop()
        if not stack:
            parts.append("<ol>")
            stack.append(level)
            parts.append(link(chapter))
        elif level == stack[-1]:
            parts.append("</li>")
            parts.append(link(chapter))
        else:
            parts.append("<ol>")
            stack.append(level)
            parts.append(link(chapter))
    while stack:
        parts.append("</li></ol>")
        stack.pop()
    return "".join(parts)


def _nav_xhtml(book: VerifiedBook, language: str) -> str:
    lang = quoteattr(language)
    entries = _nav_entries(book)
    return (
        XML_DECL
        + "\n<!DOCTYPE html>\n"
        + f'<html xmlns="{_XHTML_NS}" xmlns:epub="{_EPUB_NS}" lang={lang} xml:lang={lang}>'
        + "<head><title>Contents</title></head>"
        + f'<body><nav epub:type="toc" id="toc"><h1>Contents</h1>{entries}</nav></body>'
        + "</html>\n"
    )


def _page_xhtml(page: VerifiedPage, language: str) -> str:
    lang = quoteattr(language)
    paragraphs = "".join(f"<p>{escape(text)}</p>" for text in page.paragraphs())
    # Prefer the number printed in the book. Readers cite that number, and in a
    # book whose numbering restarts per section the physical index is meaningless
    # to them.
    index = page.printed_label or str(page.page_index + 1)
    title = f"Page {index}"
    return (
        XML_DECL
        + "\n<!DOCTYPE html>\n"
        + f'<html xmlns="{_XHTML_NS}" lang={lang} xml:lang={lang}>'
        + f"<head><title>{escape(title)}</title></head>"
        + f"<body><section>{paragraphs}</section></body>"
        + "</html>\n"
    )


def build_epub(
    book: VerifiedBook,
    *,
    identifier: str,
    language: str,
    modified: datetime,
    author: str | None = None,
) -> bytes:
    """Render a verified book as a deterministic EPUB 3 archive."""

    if not book.pages:
        raise ValueError("cannot export a book with no pages")
    validate_identifier(identifier)
    validate_language(language)
    validate_book_text(book, author=author)
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
                author=author,
            ),
            zipfile.ZIP_DEFLATED,
        )
        write_zip_entry(
            archive, "OEBPS/nav.xhtml", _nav_xhtml(book, language), zipfile.ZIP_DEFLATED
        )
        for page in book.pages:
            write_zip_entry(
                archive,
                f"OEBPS/{_page_file_name(page.page_index)}",
                _page_xhtml(page, language),
                zipfile.ZIP_DEFLATED,
            )
    data = buffer.getvalue()
    check_epub(data, book)
    return data


def write_epub(
    book: VerifiedBook,
    path: Path,
    *,
    identifier: str,
    language: str,
    modified: datetime,
    author: str | None = None,
) -> Path:
    """Write an EPUB atomically so an interrupted export never leaves a torn file."""

    return write_atomic(
        path,
        build_epub(
            book,
            identifier=identifier,
            language=language,
            modified=modified,
            author=author,
        ),
    )
