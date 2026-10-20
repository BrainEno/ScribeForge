from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scribeforge.adapters.epub import build_epub, write_epub
from scribeforge.domain.content import VerifiedBook, VerifiedPage

_MODIFIED = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {
    "o": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_XHTML_NS = {"x": "http://www.w3.org/1999/xhtml"}


def _book() -> VerifiedBook:
    return VerifiedBook(
        "Scanned <Book> & Co",
        (
            VerifiedPage(0, ("鸦", 'x < y & "z"', "")),
            VerifiedPage(1, ("二",)),
        ),
    )


def _archive(data: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(data))


def _parse(archive: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(archive.read(name))


def _build(**overrides: object) -> bytes:
    options: dict[str, object] = {
        "identifier": "urn:scribeforge:test",
        "language": "zh",
        "modified": _MODIFIED,
    }
    options.update(overrides)
    return build_epub(_book(), **options)  # type: ignore[arg-type]


def test_mimetype_is_the_first_stored_entry() -> None:
    archive = _archive(_build())

    infos = archive.infolist()

    assert infos[0].filename == "mimetype"
    assert infos[0].compress_type == zipfile.ZIP_STORED
    assert archive.read("mimetype") == b"application/epub+zip"


def test_container_points_at_the_package_document() -> None:
    archive = _archive(_build())

    container = _parse(archive, "META-INF/container.xml")
    rootfile = container.find("c:rootfiles/c:rootfile", _CONTAINER_NS)

    assert rootfile is not None
    assert rootfile.get("full-path") == "OEBPS/content.opf"
    assert rootfile.get("media-type") == "application/oebps-package+xml"


def test_package_metadata_and_spine_follow_page_order() -> None:
    archive = _archive(_build(identifier="urn:test:123"))

    opf = _parse(archive, "OEBPS/content.opf")

    assert opf.get("version") == "3.0"
    assert opf.get("unique-identifier") == "book-id"
    assert opf.find("o:metadata/dc:identifier", _OPF_NS).text == "urn:test:123"  # type: ignore[union-attr]
    assert opf.find("o:metadata/dc:title", _OPF_NS).text == "Scanned <Book> & Co"  # type: ignore[union-attr]
    assert opf.find("o:metadata/dc:language", _OPF_NS).text == "zh"  # type: ignore[union-attr]
    assert opf.find("o:metadata/o:meta", _OPF_NS).text == "2026-10-20T12:00:00Z"  # type: ignore[union-attr]
    itemrefs = [item.get("idref") for item in opf.findall("o:spine/o:itemref", _OPF_NS)]
    assert itemrefs == ["page-0", "page-1"]
    manifest = {item.get("id"): item.get("href") for item in opf.findall("o:manifest/o:item", _OPF_NS)}
    assert manifest["nav"] == "nav.xhtml"
    assert manifest["page-0"] == "page-000001.xhtml"
    assert manifest["page-1"] == "page-000002.xhtml"


def test_nav_lists_every_page() -> None:
    archive = _archive(_build())

    nav = _parse(archive, "OEBPS/nav.xhtml")

    nav_element = nav.find(".//x:nav", _XHTML_NS)
    assert nav_element is not None
    assert nav_element.get("{http://www.idpf.org/2007/ops}type") == "toc"
    hrefs = [link.get("href") for link in nav.findall(".//x:a", _XHTML_NS)]
    assert hrefs == ["page-000001.xhtml", "page-000002.xhtml"]


def test_pages_preserve_escaped_source_text() -> None:
    archive = _archive(_build())

    page = _parse(archive, "OEBPS/page-000001.xhtml")

    paragraphs = [element.text or "" for element in page.findall(".//x:p", _XHTML_NS)]
    assert paragraphs == ["鸦", 'x < y & "z"', ""]


def test_build_is_deterministic() -> None:
    assert _build() == _build()


def test_modified_timestamp_is_normalized_to_utc() -> None:
    shifted = datetime(
        2026, 10, 20, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))
    )

    archive = _archive(_build(modified=shifted))

    opf = _parse(archive, "OEBPS/content.opf")
    assert opf.find("o:metadata/o:meta", _OPF_NS).text == "2026-10-20T12:00:00Z"  # type: ignore[union-attr]


def test_build_rejects_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _build(modified=datetime(2026, 10, 20, 12, 0, 0))


def test_build_rejects_an_unsafe_language_tag() -> None:
    with pytest.raises(ValueError, match="language"):
        _build(language='zh" onload="x')


def test_build_rejects_a_book_without_pages() -> None:
    with pytest.raises(ValueError, match="no pages"):
        build_epub(
            VerifiedBook("Empty", ()),
            identifier="urn:test",
            language="zh",
            modified=_MODIFIED,
        )


def test_build_rejects_an_empty_identifier() -> None:
    with pytest.raises(ValueError, match="identifier"):
        _build(identifier="   ")


def test_package_includes_author_when_given() -> None:
    archive = _archive(_build(author="Ada Lovelace"))

    opf = _parse(archive, "OEBPS/content.opf")

    assert opf.find("o:metadata/dc:creator", _OPF_NS).text == "Ada Lovelace"  # type: ignore[union-attr]


def test_package_omits_author_when_absent() -> None:
    archive = _archive(_build())

    opf = _parse(archive, "OEBPS/content.opf")

    assert opf.find("o:metadata/dc:creator", _OPF_NS) is None


def test_package_escapes_the_author() -> None:
    archive = _archive(_build(author="A & B <C>"))

    opf = _parse(archive, "OEBPS/content.opf")

    assert opf.find("o:metadata/dc:creator", _OPF_NS).text == "A & B <C>"  # type: ignore[union-attr]


def test_build_rejects_text_that_xml_cannot_represent() -> None:
    book = VerifiedBook("Title", (VerifiedPage(0, ("fine", "bad \x0b char")),))

    with pytest.raises(ValueError, match=r"page 0 line 1.*U\+000B"):
        build_epub(book, identifier="urn:test", language="zh", modified=_MODIFIED)


def test_build_rejects_a_title_that_xml_cannot_represent() -> None:
    book = VerifiedBook("Bad \x00 title", (VerifiedPage(0, ("ok",)),))

    with pytest.raises(ValueError, match=r"title.*U\+0000"):
        build_epub(book, identifier="urn:test", language="zh", modified=_MODIFIED)


def test_build_rejects_an_author_that_xml_cannot_represent() -> None:
    with pytest.raises(ValueError, match=r"author.*U\+001F"):
        _build(author="bad \x1f author")


def test_build_rejects_an_identifier_that_xml_cannot_represent() -> None:
    with pytest.raises(ValueError, match=r"identifier.*U\+0000"):
        _build(identifier="urn:test:\x00")


def test_build_accepts_xml_whitespace_characters() -> None:
    book = VerifiedBook("Title", (VerifiedPage(0, ("a\tb", "c\nd")),))

    archive = _archive(
        build_epub(book, identifier="urn:test", language="zh", modified=_MODIFIED)
    )

    page = _parse(archive, "OEBPS/page-000001.xhtml")
    paragraphs = [element.text or "" for element in page.findall(".//x:p", _XHTML_NS)]
    assert paragraphs == ["a\tb", "c\nd"]


def test_build_epub_rechecks_the_rendered_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[int] = []
    monkeypatch.setattr(
        "scribeforge.adapters.epub.check_epub",
        lambda data, book: checked.append(len(data)),
    )

    data = _build()

    assert checked == [len(data)]


def test_write_epub_replaces_the_target_atomically(tmp_path: Path) -> None:
    path = tmp_path / "out" / "book.epub"

    written = write_epub(
        _book(), path, identifier="urn:scribeforge:test", language="zh", modified=_MODIFIED
    )

    assert written == path
    assert path.read_bytes() == _build()
    assert [entry.name for entry in path.parent.iterdir()] == ["book.epub"]
