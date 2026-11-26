from __future__ import annotations

import io
import zipfile
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image

from scribeforge.adapters.export_check import check_docx
from scribeforge.adapters.export_common import (
    XML_DECL,
    format_modified,
    validate_book_text,
    validate_identifier,
    validate_language,
    validate_normalization_policy,
    write_atomic,
    write_zip_bytes,
    write_zip_entry,
)
from scribeforge.domain.content import IMAGE_MEDIA_TYPES, VerifiedBook, VerifiedInlineFigure
from scribeforge.domain.flow import PAGE_LAYOUT, FlowParagraph, build_flow
from scribeforge.domain.paragraphs import LineRole, Paragraph

_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CORE_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_DCTERMS_NS = "http://purl.org/dc/terms/"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_IMAGE_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

_OFFICE_DOCUMENT_REL = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_CORE_PROPERTIES_REL = (
    "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
)
_DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_CORE_PROPERTIES_CONTENT_TYPE = "application/vnd.openxmlformats-package.core-properties+xml"
_RELATIONSHIPS_CONTENT_TYPE = "application/vnd.openxmlformats-package.relationships+xml"


def _content_types_xml(book: VerifiedBook) -> str:
    image_types = sorted({figure.media_type for figure in book.inline_figures})
    image_defaults = "".join(
        f'<Default Extension="{extension.lstrip(".")}" ContentType="{media_type}"/>'
        for extension, media_type in sorted(IMAGE_MEDIA_TYPES.items())
        if media_type in image_types
    )
    return (
        XML_DECL
        + "\n"
        + f'<Types xmlns="{_CONTENT_TYPES_NS}">'
        + f'<Default Extension="rels" ContentType="{_RELATIONSHIPS_CONTENT_TYPE}"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + image_defaults
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


def _document_relationships_xml(figures: tuple[VerifiedInlineFigure, ...]) -> str:
    relationships = "".join(
        f'<Relationship Id="rId{index}" Type="{_IMAGE_REL}" '
        f'Target="media/{_figure_file_name(figure)}"/>'
        for index, figure in enumerate(figures, start=1)
    )
    return (
        XML_DECL
        + "\n"
        + f'<Relationships xmlns="{_RELATIONSHIPS_NS}">{relationships}</Relationships>\n'
    )


def _figure_file_name(figure: VerifiedInlineFigure) -> str:
    extension = next(
        (
            suffix
            for suffix, media_type in IMAGE_MEDIA_TYPES.items()
            if media_type == figure.media_type
        ),
        None,
    )
    if extension is None:
        raise ValueError(f"unsupported inline figure media type: {figure.media_type!r}")
    return f"inline-{figure.page_index + 1:06d}-{figure.confirmation_id:06d}{extension}"


def _core_properties_xml(
    book: VerifiedBook,
    *,
    identifier: str,
    language: str,
    modified: str,
    author: str | None,
    normalization: str,
) -> str:
    creator = "" if author is None else f"<dc:creator>{escape(author)}</dc:creator>"
    return (
        XML_DECL
        + "\n"
        + f'<cp:coreProperties xmlns:cp="{_CORE_NS}" xmlns:dc="{_DC_NS}" '
        + f'xmlns:dcterms="{_DCTERMS_NS}" xmlns:xsi="{_XSI_NS}">'
        + f"<dc:title>{escape(book.title)}</dc:title>"
        + creator
        + f"<dc:identifier>{escape(identifier)}</dc:identifier>"
        + f"<dc:language>{escape(language)}</dc:language>"
        + f"<dc:description>ScribeForge normalization: {escape(normalization)}</dc:description>"
        + f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified>'
        + "</cp:coreProperties>\n"
    )


def _paragraph_xml(text: str) -> str:
    if text == "":
        return "<w:p/>"
    return f'<w:p><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>'


def _block_xml(block: Paragraph | FlowParagraph) -> str:
    """Render a paragraph with the structure its role implies.

    Word's built-in ``Heading2`` style is what turns a chapter title into a
    navigation entry in Word and in anything that reads the document outline;
    plain text hides it among the body copy. A footnote keeps its text but is set
    apart, so it is not mistaken for the sentence it annotates.
    """

    text = escape(block.text)
    if block.role is LineRole.HEADING:
        return (
            '<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
            f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
        )
    if block.role is LineRole.FOOTNOTE:
        return (
            f'<w:p><w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr>'
            f'<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
        )
    return f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _inline_figure_xml(figure: VerifiedInlineFigure, relationship_id: int, drawing_id: int) -> str:
    with Image.open(io.BytesIO(figure.data)) as image:
        width, height = image.size
    if width < 1 or height < 1:
        raise ValueError("inline figure has invalid dimensions")
    max_width, max_height = 5_486_400, 6_858_000  # 6 x 7.5 inches in EMUs
    width_emu, height_emu = width * 9525, height * 9525
    scale = min(max_width / width_emu, max_height / height_emu, 1.0)
    cx, cy = max(1, round(width_emu * scale)), max(1, round(height_emu * scale))
    name = escape(_figure_file_name(figure), {'"': "&quot;"})
    alt = escape(figure.alt or figure.caption, {'"': "&quot;"})
    image_xml = (
        '<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{drawing_id}" name="{name}" descr="{alt}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        f'<a:graphic><a:graphicData uri="{_PIC_NS}"><pic:pic>'
        '<pic:nvPicPr><pic:cNvPr id="0" name="image"/><pic:cNvPicPr/></pic:nvPicPr>'
        "<pic:blipFill>"
        f'<a:blip r:embed="rId{relationship_id}"/>'
        "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
        '<pic:spPr><a:xfrm><a:off x="0" y="0"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )
    if not figure.caption:
        return image_xml
    caption = escape(figure.caption)
    return image_xml + (
        '<w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr>'
        f'<w:r><w:t xml:space="preserve">{caption}</w:t></w:r></w:p>'
    )


def _document_xml(book: VerifiedBook) -> str:
    paragraphs: list[str] = []
    flow = build_flow(book, layout=PAGE_LAYOUT)
    documents_by_page = {
        page_index: document for document in flow.documents for page_index in document.pages
    }
    figures = tuple(
        figure
        for _anchor, figure in sorted(
            (placement for document in flow.documents for placement in document.inline_figures),
            key=lambda item: (
                item[1].page_index,
                item[1].reading_position,
                item[1].confirmation_id,
            ),
        )
    )
    relationship_ids = {
        figure.confirmation_id: index for index, figure in enumerate(figures, start=1)
    }
    drawing_ids = {figure.confirmation_id: index for index, figure in enumerate(figures, start=1)}
    for index, page in enumerate(book.pages):
        if index > 0:
            paragraphs.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        document = documents_by_page.get(page.page_index)
        if document is None or not document.inline_figures:
            paragraphs.extend(_block_xml(block) for block in page.blocks())
            continue
        slots: dict[int, list[VerifiedInlineFigure]] = {}
        for anchor, figure in document.inline_figures:
            slots.setdefault(anchor, []).append(figure)
        for paragraph_index in range(len(document.paragraphs) + 1):
            for figure in slots.get(paragraph_index, []):
                paragraphs.append(
                    _inline_figure_xml(
                        figure,
                        relationship_ids[figure.confirmation_id],
                        drawing_ids[figure.confirmation_id],
                    )
                )
            if paragraph_index < len(document.paragraphs):
                paragraphs.append(_block_xml(document.paragraphs[paragraph_index]))
    return (
        XML_DECL
        + "\n"
        + f'<w:document xmlns:w="{_WORD_NS}" xmlns:r="{_R_NS}" '
        + f'xmlns:wp="{_WP_NS}" xmlns:a="{_A_NS}" xmlns:pic="{_PIC_NS}"><w:body>'
        + "".join(paragraphs)
        + "<w:sectPr/></w:body></w:document>\n"
    )


def build_docx(
    book: VerifiedBook,
    *,
    identifier: str,
    language: str,
    modified: datetime,
    author: str | None = None,
    normalization: str = "preserve",
) -> bytes:
    """Render a verified book as a deterministic WordprocessingML document."""

    if not book.pages:
        raise ValueError("cannot export a book with no pages")
    validate_identifier(identifier)
    validate_language(language)
    normalization = validate_normalization_policy(normalization)
    validate_book_text(book, author=author)
    modified_text = format_modified(modified)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        write_zip_entry(
            archive, "[Content_Types].xml", _content_types_xml(book), zipfile.ZIP_DEFLATED
        )
        write_zip_entry(archive, "_rels/.rels", _package_relationships_xml(), zipfile.ZIP_DEFLATED)
        write_zip_entry(archive, "word/document.xml", _document_xml(book), zipfile.ZIP_DEFLATED)
        if book.inline_figures:
            figures = tuple(
                sorted(
                    book.inline_figures,
                    key=lambda item: (item.page_index, item.reading_position, item.confirmation_id),
                )
            )
            write_zip_entry(
                archive,
                "word/_rels/document.xml.rels",
                _document_relationships_xml(figures),
                zipfile.ZIP_DEFLATED,
            )
            for figure in figures:
                write_zip_bytes(
                    archive,
                    f"word/media/{_figure_file_name(figure)}",
                    figure.data,
                    zipfile.ZIP_DEFLATED,
                )
        write_zip_entry(
            archive,
            "docProps/core.xml",
            _core_properties_xml(
                book,
                identifier=identifier,
                language=language,
                modified=modified_text,
                author=author,
                normalization=normalization,
            ),
            zipfile.ZIP_DEFLATED,
        )
    data = buffer.getvalue()
    check_docx(data, book)
    return data


def write_docx(
    book: VerifiedBook,
    path: Path,
    *,
    identifier: str,
    language: str,
    modified: datetime,
    author: str | None = None,
    normalization: str = "preserve",
) -> Path:
    """Write a DOCX atomically so an interrupted export never leaves a torn file."""

    return write_atomic(
        path,
        build_docx(
            book,
            identifier=identifier,
            language=language,
            modified=modified,
            author=author,
            normalization=normalization,
        ),
    )
