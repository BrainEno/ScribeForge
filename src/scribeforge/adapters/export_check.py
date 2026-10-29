"""Post-render integrity checks for exported books.

Export is the last place where evidence could be lost, so the builders re-read
their own output: every part must parse, every manifest/relationship target must
exist, and the text must still be exactly the verified book, page by page.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from scribeforge.adapters.export_common import epub_image_name
from scribeforge.domain.content import VerifiedBook
from scribeforge.domain.flow import AUTO_LAYOUT, BookFlow, build_flow

_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {
    "o": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_XHTML_NS = {"x": "http://www.w3.org/1999/xhtml"}
_EPUB_NS = {"epub": "http://www.idpf.org/2007/ops"}
_CONTENT_TYPES_NS = {"ct": "http://schemas.openxmlformats.org/package/2006/content-types"}
_RELATIONSHIPS_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_WORD_NS_URI = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD_NS = {"w": _WORD_NS_URI}
_DC_NS = {"dc": "http://purl.org/dc/elements/1.1/"}


class ExportIntegrityError(ValueError):
    """Raised when a rendered archive does not match the verified book."""


def _open(data: bytes) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExportIntegrityError("export is not a zip archive") from exc
    corrupt = archive.testzip()
    if corrupt is not None:
        raise ExportIntegrityError(f"corrupt archive entry: {corrupt}")
    return archive


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ExportIntegrityError(message)


def _parse(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(archive.read(name))
    except (KeyError, ET.ParseError) as exc:
        raise ExportIntegrityError(f"{name} is not well-formed XML: {exc}") from exc


def _parse_all(archive: zipfile.ZipFile, names: Iterable[str]) -> None:
    for name in names:
        _parse(archive, name)


def _entry_text(element: ET.Element | None) -> str:
    if element is None or element.text is None:
        return ""
    return element.text


def _xml_names(names: Iterable[str]) -> list[str]:
    return sorted(
        name for name in names if name.lower().endswith((".xml", ".xhtml", ".opf", ".rels"))
    )


def _resolve(base_file: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_file), href))


def _relationship_base(name: str) -> str:
    return posixpath.dirname(posixpath.dirname(name))


def check_epub(
    data: bytes, book: VerifiedBook, *, layout: str = AUTO_LAYOUT
) -> None:
    """Verify a rendered EPUB 3 archive against the verified book."""

    with _open(data) as archive:
        infos = archive.infolist()
        _require(
            bool(infos) and infos[0].filename == "mimetype",
            "mimetype must be the first archive entry",
        )
        _require(
            infos[0].compress_type == zipfile.ZIP_STORED,
            "mimetype must be stored uncompressed",
        )
        _require(
            archive.read("mimetype") == b"application/epub+zip",
            "mimetype must be application/epub+zip",
        )
        names = set(archive.namelist())

        _require("META-INF/container.xml" in names, "missing META-INF/container.xml")
        container = _parse(archive, "META-INF/container.xml")
        rootfile = container.find("c:rootfiles/c:rootfile", _CONTAINER_NS)
        if rootfile is None:
            raise ExportIntegrityError("container.xml declares no rootfile")
        package_name = rootfile.get("full-path") or ""
        _require(package_name in names, f"package document {package_name!r} is missing")

        _parse_all(archive, _xml_names(names))

        package = _parse(archive, package_name)
        manifest: dict[str, str] = {}
        for item in package.findall("o:manifest/o:item", _OPF_NS):
            item_id = item.get("id") or ""
            href = item.get("href") or ""
            manifest[item_id] = href
            _require(
                _resolve(package_name, href) in names,
                f"manifest item {item_id!r} points at missing {href!r}",
            )

        flow = build_flow(book, layout=layout)
        expected_spine = [document.item_id for document in flow.documents]
        spine = [
            item.get("idref") or ""
            for item in package.findall("o:spine/o:itemref", _OPF_NS)
        ]
        _require(spine == expected_spine, "spine does not follow the verified page order")
        for idref in spine:
            _require(idref in manifest, f"spine itemref {idref!r} is not in the manifest")

        _require(
            _entry_text(package.find("o:metadata/dc:title", _OPF_NS)) == book.title,
            "package title differs from the book",
        )

        references: Counter[str] = Counter()
        _check_images(archive, package, manifest, book)
        _check_nav(archive, package, manifest, package_name)

        for expected in flow.documents:
            document_name = _resolve(package_name, manifest[expected.item_id])
            rendered = _xhtml_blocks(_parse(archive, document_name))
            _require(
                rendered.paragraphs
                == [paragraph.text for paragraph in expected.paragraphs],
                f"document {expected.file_name} text differs from the verified book",
            )
            _require(
                rendered.pages == [page_index for page_index, _ in expected.page_labels],
                f"document {expected.file_name} does not carry a marker per scanned page",
            )
            _require(
                sorted(_resolve(document_name, source) for source in rendered.images)
                == sorted(epub_image_name(image) for image in expected.images),
                f"document {expected.file_name} does not show exactly its own images",
            )
            for source in rendered.images:
                references[_resolve(document_name, source)] += 1
            covered = set(expected.pages)
            accepted = "".join(
                line for page in book.pages if page.page_index in covered for line in page.lines
            )
            _require(
                _squash("".join(rendered.paragraphs)) == _squash(accepted),
                f"document {expected.file_name} lost or gained text",
            )
        _require_no_text_lost(book)
        _require_flow_preserves_pages(book, flow)
        _require_images_referenced_once(book, references)


def _check_images(
    archive: zipfile.ZipFile,
    package: ET.Element,
    manifest: dict[str, str],
    book: VerifiedBook,
) -> None:
    """Verify every image the book carries, and how the package declares it.

    Two independent claims have to hold. The bytes written must be the bytes the
    book recorded (same SHA-256), which is what stops a re-rendered or swapped
    page raster from entering an export unnoticed; and the archive must declare
    exactly the images the book has — one manifest item each, one cover, and no
    orphan file that no document shows. How many documents *show* each image is
    counted by the caller, which knows the spine.
    """

    names = set(archive.namelist())
    expected = {epub_image_name(image): image for image in book.images}
    for name, image in expected.items():
        _require(name in names, f"page {image.page_index} image is missing from the archive")
        actual = hashlib.sha256(archive.read(name)).hexdigest()
        _require(
            actual == image.sha256,
            f"page {image.page_index} image bytes differ from the verified page raster",
        )

    strays = sorted(
        name for name in names if name.startswith("OEBPS/images/") and name not in expected
    )
    _require(not strays, f"archive carries images the book does not claim: {strays[:3]}")

    declared: dict[str, tuple[str, str]] = {}
    for item in package.findall("o:manifest/o:item", _OPF_NS):
        if not (item.get("media-type") or "").startswith("image/"):
            continue
        declared[item.get("href") or ""] = (
            item.get("id") or "",
            item.get("properties") or "",
        )
    _require(
        set(declared) == {posixpath.relpath(name, "OEBPS") for name in expected},
        "manifest image items do not match the book's images",
    )
    for name in expected:
        href = posixpath.relpath(name, "OEBPS")
        item_id, _ = declared[href]
        _require(
            manifest.get(item_id) == href,
            f"manifest item {item_id!r} does not point at {href!r}",
        )

    cover = book.cover_image
    cover_items = sorted(
        href for href, (_, properties) in declared.items() if "cover-image" in properties.split()
    )
    if cover is None:
        _require(not cover_items, "package declares a cover image the book does not have")
        _require(
            package.find("o:metadata/o:meta[@name='cover']", _OPF_NS) is None,
            "package declares a cover the book does not have",
        )
    else:
        cover_href = posixpath.relpath(epub_image_name(cover), "OEBPS")
        _require(
            cover_items == [cover_href],
            "package does not declare exactly the book's cover image",
        )
        meta = package.find("o:metadata/o:meta[@name='cover']", _OPF_NS)
        _require(
            meta is not None and meta.get("content") == declared[cover_href][0],
            "package does not point EPUB 2 readers at the cover image",
        )


def _check_nav(
    archive: zipfile.ZipFile,
    package: ET.Element,
    manifest: dict[str, str],
    package_name: str,
) -> None:
    """A reading system needs a navigation document it can actually use.

    EPUB 3 reading systems (Readium, epub.js and most library grids) require:

    * an ``OEBPS/nav.xhtml`` navigation document whose ``<nav>`` carries
      ``epub:type="toc"``;
    * a manifest item ``nav`` with ``properties="nav"`` pointing at it;
    * every spine document reachable from that navigation — a book a reader
      cannot navigate to is effectively missing.
    """

    names = set(archive.namelist())
    nav_name = "OEBPS/nav.xhtml"
    _require(nav_name in names, "missing OEBPS/nav.xhtml navigation document")
    nav = _parse(archive, nav_name)
    nav_element = nav.find(".//x:nav", _XHTML_NS)
    _require(nav_element is not None, "nav document has no <nav> element")
    assert nav_element is not None
    _require(
        nav_element.get("{http://www.idpf.org/2007/ops}type") == "toc",
        "nav document's <nav> is not epub:type=\"toc\"",
    )

    nav_items = [
        item
        for item in package.findall("o:manifest/o:item", _OPF_NS)
        if item.get("id") == "nav"
    ]
    _require(len(nav_items) == 1, "manifest must declare exactly one nav item")
    nav_item = nav_items[0]
    _require(
        nav_item.get("media-type") == "application/xhtml+xml",
        "nav manifest item must be application/xhtml+xml",
    )
    _require(
        "nav" in (nav_item.get("properties") or "").split(),
        "nav manifest item must carry properties=\"nav\"",
    )
    _require(
        manifest.get("nav") == posixpath.relpath(nav_name, "OEBPS"),
        "nav manifest item must point at OEBPS/nav.xhtml",
    )

    spine_hrefs = [
        manifest[item.get("idref") or ""]
        for item in package.findall("o:spine/o:itemref", _OPF_NS)
    ]
    nav_hrefs = [link.get("href") or "" for link in nav.findall(".//x:a", _XHTML_NS)]
    for href in spine_hrefs:
        _require(
            href in nav_hrefs,
            f"spine document {href!r} is not reachable from the navigation",
        )


def _require_images_referenced_once(book: VerifiedBook, seen: Counter[str]) -> None:
    """Each image must be shown exactly once, by the document that owns its page."""

    for image in book.images:
        name = epub_image_name(image)
        _require(
            seen[name] == 1,
            f"page {image.page_index} image is shown {seen[name]} time(s) instead of once",
        )


def _squash(text: str) -> str:
    """Drop whitespace so an intentional join space is not read as a lost character.

    Joining two visual lines that break inside a Latin word needs the space the
    typesetter put there. That space is added *between* characters that already
    existed, so comparing with whitespace removed still catches a swallowed line
    while tolerating the join.
    """

    return "".join(text.split())


@dataclass(frozen=True, slots=True)
class _Rendered:
    """What one rendered document contains.

    Text blocks, page markers and image references: the three things the writer
    decides about a document, and therefore the three things the check has to be
    able to read back.
    """

    paragraphs: list[str]
    pages: list[int]
    images: list[str]


def _xhtml_blocks(document: ET.Element) -> _Rendered:
    """Read a document's rendered blocks back, in document order.

    Headings, body paragraphs and footnotes are separate elements now, so a flat
    ``.//p`` query would silently skip every heading and reorder notes. Reading the
    section's children in order is the only comparison that matches what a reader
    actually receives. ``pagebreak`` spans are read too: they are how the export
    proves the book's own pagination survived the regrouping.
    """

    section = document.find(".//x:section", _XHTML_NS)
    if section is None:
        return _Rendered([], [], [])
    xhtml = _XHTML_NS["x"]
    text_block_tags = {f"{{{xhtml}}}h1", f"{{{xhtml}}}h2", f"{{{xhtml}}}p"}
    text: list[str] = []
    pages: list[int] = []
    images: list[str] = []
    for child in section:
        if child.tag in text_block_tags:
            text.append(child.text or "")
        elif child.tag == f"{{{xhtml}}}aside":
            text.extend(element.text or "" for element in child.findall("x:p", _XHTML_NS))
        elif child.tag == f"{{{xhtml}}}span":
            identifier = child.get("id") or ""
            if not identifier.startswith("page-"):
                raise ExportIntegrityError(f"unexpected span in a document: {identifier!r}")
            pages.append(int(identifier.removeprefix("page-")))
        elif child.tag == f"{{{xhtml}}}figure":
            images.extend(_figure_sources(child))
        else:
            raise ExportIntegrityError(f"unexpected page element: {child.tag}")
    return _Rendered(text, pages, images)


def _figure_sources(figure: ET.Element) -> list[str]:
    """Every ``img`` source in a figure, refusing an image with no alternative text."""

    sources: list[str] = []
    for element in figure.findall("x:img", _XHTML_NS):
        source = element.get("src") or ""
        if not source:
            raise ExportIntegrityError("figure contains an img without a source")
        if not (element.get("alt") or "").strip():
            raise ExportIntegrityError(f"image {source!r} has no alternative text")
        sources.append(source)
    if not sources:
        raise ExportIntegrityError("figure contains no image")
    return sources


def _require_flow_preserves_pages(book: VerifiedBook, flow: BookFlow) -> None:
    """Every scanned page must appear exactly once, in order, across the documents.

    A paragraph may be joined across a page turn, so text cannot be compared page by
    page any more. What must still hold is that the pages themselves are neither
    dropped, duplicated nor reordered — the failure that would silently shorten a
    book.
    """

    placed = [
        page_index for document in flow.documents for page_index, _ in document.page_labels
    ]
    expected = [page.page_index for page in book.pages]
    _require(
        sorted(placed) == sorted(expected) and placed == sorted(placed),
        "flow documents do not place every scanned page exactly once, in order",
    )


def _require_no_text_lost(book: VerifiedBook) -> None:
    """Splicing may merge lines but must never drop or add a character.

    A vertical paragraph is assembled by joining columns, so the paragraph list is
    a *re-grouping* of the accepted lines. Comparing the two concatenations catches
    a splice rule that quietly swallowed a line — the one failure mode that would
    survive a paragraph-by-paragraph comparison.
    """

    for page in book.pages:
        joined = _squash("".join(page.paragraphs()))
        accepted = _squash("".join(page.lines))
        _require(
            joined == accepted,
            f"page {page.page_index} lost or gained text while splicing paragraphs",
        )


def check_docx(data: bytes, book: VerifiedBook) -> None:
    """Verify a rendered WordprocessingML package against the verified book."""

    with _open(data) as archive:
        infos = archive.infolist()
        _require(
            bool(infos) and infos[0].filename == "[Content_Types].xml",
            "[Content_Types].xml must be the first archive entry",
        )
        names = set(archive.namelist())
        _require("[Content_Types].xml" in names, "missing [Content_Types].xml")

        _parse_all(archive, _xml_names(names))

        content_types = _parse(archive, "[Content_Types].xml")
        overrides = {
            (item.get("PartName") or "").lstrip("/"): item.get("ContentType") or ""
            for item in content_types.findall("ct:Override", _CONTENT_TYPES_NS)
        }
        for part in overrides:
            _require(part in names, f"content-type override points at missing part {part!r}")
        defaults = {
            (item.get("Extension") or "").lower()
            for item in content_types.findall("ct:Default", _CONTENT_TYPES_NS)
        }
        for name in sorted(names):
            if name == "[Content_Types].xml":
                continue
            extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            _require(
                name in overrides or extension in defaults,
                f"part {name!r} has no content type",
            )

        for name in sorted(entry for entry in names if entry.endswith(".rels")):
            relationships = _parse(archive, name)
            base = _relationship_base(name)
            for relationship in relationships.findall("r:Relationship", _RELATIONSHIPS_NS):
                target = relationship.get("Target") or ""
                _require(
                    posixpath.normpath(posixpath.join(base, target)) in names,
                    f"relationship {target!r} points at missing part",
                )

        _require("word/document.xml" in names, "missing word/document.xml")
        document = _parse(archive, "word/document.xml")
        page_texts: list[list[str]] = [[]]
        for paragraph in document.findall("w:body/w:p", _WORD_NS):
            if any(
                br.get(f"{{{_WORD_NS_URI}}}type") == "page"
                for br in paragraph.findall(".//w:br", _WORD_NS)
            ):
                page_texts.append([])
                continue
            page_texts[-1].append(
                "".join(
                    token.text or "" for token in paragraph.iter(f"{{{_WORD_NS_URI}}}t")
                )
            )
        expected = [list(page.paragraphs()) for page in book.pages]
        _require(
            len(page_texts) == len(expected),
            f"document has {len(page_texts)} page(s), expected {len(expected)}",
        )
        for page, actual in zip(book.pages, page_texts):
            _require(
                actual == list(page.paragraphs()),
                f"page {page.page_index} text differs from the verified book",
            )
        _require_no_text_lost(book)

        _require("docProps/core.xml" in names, "missing docProps/core.xml")
        core = _parse(archive, "docProps/core.xml")
        _require(
            _entry_text(core.find("dc:title", _DC_NS)) == book.title,
            "core properties title differs from the book",
        )
