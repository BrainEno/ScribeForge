"""Post-render integrity checks for exported books.

Export is the last place where evidence could be lost, so the builders re-read
their own output: every part must parse, every manifest/relationship target must
exist, and the text must still be exactly the verified book, page by page.
"""

from __future__ import annotations

import io
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable

from scribeforge.domain.content import VerifiedBook

_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {
    "o": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_XHTML_NS = {"x": "http://www.w3.org/1999/xhtml"}
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


def check_epub(data: bytes, book: VerifiedBook) -> None:
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

        expected_spine = [f"page-{page.page_index}" for page in book.pages]
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

        for page in book.pages:
            document = _parse(
                archive, _resolve(package_name, manifest[f"page-{page.page_index}"])
            )
            paragraphs = [
                element.text or "" for element in document.findall(".//x:p", _XHTML_NS)
            ]
            _require(
                paragraphs == list(page.lines),
                f"page {page.page_index} text differs from the verified book",
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
        expected = [list(page.lines) for page in book.pages]
        _require(
            len(page_texts) == len(expected),
            f"document has {len(page_texts)} page(s), expected {len(expected)}",
        )
        for page, actual in zip(book.pages, page_texts):
            _require(
                actual == list(page.lines),
                f"page {page.page_index} text differs from the verified book",
            )

        _require("docProps/core.xml" in names, "missing docProps/core.xml")
        core = _parse(archive, "docProps/core.xml")
        _require(
            _entry_text(core.find("dc:title", _DC_NS)) == book.title,
            "core properties title differs from the book",
        )
