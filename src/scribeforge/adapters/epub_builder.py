"""The single EPUB 3 package builder shared by every export layer (#72).

The trusted exporter (accepted decisions only) and the candidate exporter (raw
OCR for A/B comparison) are the same builder in two modes, selected by
``unverified``: the container skeleton, OPF manifest/spine, nav document and
integrity check are written once here, so a format fix can no longer drift
between the two layers. The candidate mode fails closed unless the
UNVERIFIED warning is present in the metadata, and the trusted mode fails
closed if it ever is — an unverified candidate can never be mistaken for a
trusted export.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr

from scribeforge.adapters.export_common import (
    MIMETYPE,
    XML_DECL,
    container_xml,
    require_xml_text,
    validate_identifier,
    validate_language,
    write_zip_bytes,
    write_zip_entry,
)

OPF_NS = "http://www.idpf.org/2007/opf"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
DC_NS = "http://purl.org/dc/elements/1.1/"
_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_MAP = {"o": OPF_NS}

#: Marker every unverified candidate package must carry in its metadata.
UNVERIFIED_CANDIDATE_WARNING = "UNVERIFIED OCR CANDIDATE — not accepted or proofread"

_NORMALIZATION_PREFIX = (
    'prefix="scribeforge: https://github.com/BrainEno/ScribeForge/normalization#"'
)


@dataclass(frozen=True, slots=True)
class EpubDocument:
    """One content document of the package (a chapter or a source page)."""

    item_id: str
    file_name: str  # inside OEBPS/
    xhtml: str
    nav_label: str
    nav_level: int = 1


@dataclass(frozen=True, slots=True)
class EpubImage:
    """One page raster referenced from the manifest."""

    item_id: str
    href: str  # manifest href, relative to OEBPS/
    media_type: str
    data: bytes
    cover: bool = False


def _nav_entries(documents: Sequence[EpubDocument]) -> str:
    """Nested table of contents; flat documents produce one flat list.

    Nested lists are emitted with an explicit stack of open levels. Building the
    closing tags by string arithmetic on levels is easy to get wrong, and the
    first attempt produced a mismatched-tag XHTML that the integrity check
    caught — which is exactly why that check exists.
    """

    parts: list[str] = []
    stack: list[int] = []
    for document in documents:
        link = f'<li><a href="{document.file_name}">{escape(document.nav_label)}</a>'
        level = max(1, document.nav_level)
        while stack and level < stack[-1]:
            parts.append("</li></ol>")
            stack.pop()
        if not stack:
            parts.append("<ol>")
            stack.append(level)
            parts.append(link)
        elif level == stack[-1]:
            parts.append("</li>")
            parts.append(link)
        else:
            parts.append("<ol>")
            stack.append(level)
            parts.append(link)
    while stack:
        parts.append("</li></ol>")
        stack.pop()
    return "".join(parts)


def _nav_xhtml(nav_title: str, language: str, documents: Sequence[EpubDocument]) -> str:
    lang = quoteattr(language)
    entries = _nav_entries(documents)
    return (
        XML_DECL
        + "\n<!DOCTYPE html>\n"
        + f'<html xmlns="{XHTML_NS}" xmlns:epub="{EPUB_NS}" lang={lang} xml:lang={lang}>'
        + f"<head><title>{escape(nav_title)}</title></head>"
        + f'<body><nav epub:type="toc" id="toc"><h1>Contents</h1>{entries}</nav></body>'
        + "</html>\n"
    )


def _package_xml(
    *,
    title: str,
    identifier: str,
    language: str,
    modified: str,
    author: str | None,
    description: str | None,
    normalization: str | None,
    documents: Sequence[EpubDocument],
    images: Sequence[EpubImage],
) -> str:
    items = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
    ]
    spine: list[str] = []
    for document in documents:
        items.append(
            f'<item id="{document.item_id}" href="{document.file_name}" '
            'media-type="application/xhtml+xml"/>'
        )
        spine.append(f'<itemref idref="{document.item_id}"/>')
    cover = next((image for image in images if image.cover), None)
    for image in images:
        properties = ' properties="cover-image"' if image is cover else ""
        items.append(
            f'<item id="{image.item_id}" href="{image.href}" '
            f'media-type="{image.media_type}"{properties}/>'
        )
    # EPUB 2 readers (and most library grids) only look at this meta name; EPUB 3
    # uses the manifest ``cover-image`` property, so both are written.
    cover_meta = "" if cover is None else f'<meta name="cover" content="{cover.item_id}"/>'
    creator = "" if author is None else f"<dc:creator>{escape(author)}</dc:creator>"
    description_meta = (
        "" if description is None else f"<dc:description>{escape(description)}</dc:description>"
    )
    prefix = "" if normalization is None else f" {_NORMALIZATION_PREFIX}"
    normalization_meta = (
        ""
        if normalization is None
        else f'<meta property="scribeforge:normalization">{escape(normalization)}</meta>'
    )
    return (
        XML_DECL
        + "\n"
        + f'<package xmlns="{OPF_NS}" version="3.0" unique-identifier="book-id"{prefix}>'
        + f'<metadata xmlns:dc="{DC_NS}">'
        + f'<dc:identifier id="book-id">{escape(identifier)}</dc:identifier>'
        + f"<dc:title>{escape(title)}</dc:title>"
        + creator
        + f"<dc:language>{escape(language)}</dc:language>"
        + description_meta
        + f'<meta property="dcterms:modified">{modified}</meta>'
        + normalization_meta
        + cover_meta
        + "</metadata>"
        + f"<manifest>{''.join(items)}</manifest>"
        + f"<spine>{''.join(spine)}</spine>"
        + "</package>\n"
    )


def _check_structure(
    data: bytes, documents: Sequence[EpubDocument], images: Sequence[EpubImage]
) -> None:
    """Re-read the rendered archive: every part must parse and be referenced."""

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if archive.testzip() is not None:
                raise ValueError("EPUB archive checksum failed")
            infos = archive.infolist()
            if not infos or infos[0].filename != "mimetype":
                raise ValueError("mimetype must be the first archive entry")
            if infos[0].compress_type != zipfile.ZIP_STORED:
                raise ValueError("mimetype must be stored uncompressed")
            if archive.read("mimetype") != MIMETYPE.encode("ascii"):
                raise ValueError("EPUB has an invalid mimetype entry")
            names = set(archive.namelist())
            for required in ("META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml"):
                if required not in names:
                    raise ValueError(f"EPUB is missing {required}")
            container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
            rootfile = container.find("c:rootfiles/c:rootfile", _CONTAINER_NS)
            if rootfile is None or (rootfile.get("full-path") or "") not in names:
                raise ValueError("container.xml does not point at an existing package document")
            for name in sorted(names):
                if name.endswith((".xml", ".opf", ".xhtml")):
                    ElementTree.fromstring(archive.read(name))
            package = ElementTree.fromstring(archive.read("OEBPS/content.opf"))
            manifest = package.find("o:manifest", _OPF_MAP)
            spine = package.find("o:spine", _OPF_MAP)
            if manifest is None or spine is None:
                raise ValueError("package document has no manifest/spine")
            item_ids = {item.get("id") for item in manifest.findall("o:item", _OPF_MAP)}
            hrefs = {item.get("href") for item in manifest.findall("o:item", _OPF_MAP)}
            nav_items = [i for i in item_ids if i == "nav"]
            if len(nav_items) != 1:
                raise ValueError("package manifest must declare exactly one nav item")
            for ref in spine.findall("o:itemref", _OPF_MAP):
                if ref.get("idref") not in item_ids:
                    raise ValueError("spine references an undeclared manifest item")
            for document in documents:
                entry = f"OEBPS/{document.file_name}"
                if entry not in names:
                    raise ValueError(f"content document {entry} is missing from the archive")
                if document.file_name not in hrefs and document.item_id not in item_ids:
                    raise ValueError(f"content document {document.item_id} is not in the manifest")
            for image in images:
                if f"OEBPS/{image.href}" not in names:
                    raise ValueError(f"image {image.href} is missing from the archive")
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ValueError(f"EPUB failed its structural integrity check: {exc}") from exc


def build_epub_package(
    *,
    title: str,
    identifier: str,
    language: str,
    modified: str,
    documents: Sequence[EpubDocument],
    images: Sequence[EpubImage] = (),
    author: str | None = None,
    description: str | None = None,
    normalization: str | None = None,
    nav_title: str = "Contents",
    unverified: bool = False,
) -> bytes:
    """Render one deterministic EPUB 3 archive from content-agnostic parts.

    ``unverified=False`` is the trusted mode (accepted decisions only);
    ``unverified=True`` is the candidate downgrade mode and requires the
    UNVERIFIED warning in the metadata, while the trusted mode rejects it.
    """

    if not documents:
        raise ValueError("cannot build an EPUB with no content documents")
    validate_identifier(identifier)
    validate_language(language)
    require_xml_text(title, context="book title")
    for field, value in (("author", author), ("description", description)):
        if value is not None:
            require_xml_text(value, context=field)
    if unverified and UNVERIFIED_CANDIDATE_WARNING not in f"{title}{description or ''}":
        raise ValueError(
            f"unverified candidate metadata must carry the {UNVERIFIED_CANDIDATE_WARNING!r} marker"
        )
    if not unverified and UNVERIFIED_CANDIDATE_WARNING in f"{title}{description or ''}":
        raise ValueError("trusted export metadata must not carry the UNVERIFIED candidate marker")

    item_ids = [document.item_id for document in documents]
    file_names = [document.file_name for document in documents]
    if len(set(item_ids)) != len(item_ids) or any(not i for i in item_ids):
        raise ValueError("EPUB content documents must have unique non-empty item ids")
    if len(set(file_names)) != len(file_names) or any(
        not name or "/" in name or name.startswith(".") for name in file_names
    ):
        raise ValueError("EPUB content documents must have unique safe file names")
    image_ids = [image.item_id for image in images]
    if len(set(image_ids)) != len(image_ids) or any(not i for i in image_ids):
        raise ValueError("EPUB images must have unique non-empty item ids")
    if any(posixpath.normpath(f"OEBPS/{image.href}") != f"OEBPS/{image.href}" for image in images):
        raise ValueError("EPUB image hrefs must stay inside OEBPS/")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        write_zip_entry(archive, "mimetype", MIMETYPE, zipfile.ZIP_STORED)
        write_zip_entry(archive, "META-INF/container.xml", container_xml(), zipfile.ZIP_DEFLATED)
        write_zip_entry(
            archive,
            "OEBPS/content.opf",
            _package_xml(
                title=title,
                identifier=identifier,
                language=language,
                modified=modified,
                author=author,
                description=description,
                normalization=normalization,
                documents=documents,
                images=images,
            ),
            zipfile.ZIP_DEFLATED,
        )
        write_zip_entry(
            archive,
            "OEBPS/nav.xhtml",
            _nav_xhtml(nav_title, language, documents),
            zipfile.ZIP_DEFLATED,
        )
        for document in documents:
            write_zip_entry(
                archive,
                f"OEBPS/{document.file_name}",
                document.xhtml,
                zipfile.ZIP_DEFLATED,
            )
        # Page rasters are already compressed, so storing them keeps the archive
        # deterministic and avoids spending CPU to grow PNGs by a few bytes.
        for image in images:
            write_zip_bytes(
                archive,
                f"OEBPS/{image.href}",
                image.data,
                zipfile.ZIP_STORED,
            )
    data = buffer.getvalue()
    _check_structure(data, documents, images)
    return data
