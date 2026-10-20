from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from scribeforge.adapters.docx import build_docx, write_docx
from scribeforge.domain.content import VerifiedBook, VerifiedPage

_MODIFIED = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = {"w": _WORD_NS}
_REL = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_CT = {"ct": "http://schemas.openxmlformats.org/package/2006/content-types"}
_CORE = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}


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
    return build_docx(_book(), **options)  # type: ignore[arg-type]


def _paragraphs(root: ET.Element) -> list[str]:
    result: list[str] = []
    for paragraph in root.findall("w:body/w:p", _W):
        breaks = paragraph.findall(".//w:br", _W)
        if any(item.get(f"{{{_WORD_NS}}}type") == "page" for item in breaks):
            result.append("<break>")
            continue
        result.append(
            "".join(token.text or "" for token in paragraph.iter(f"{{{_WORD_NS}}}t"))
        )
    return result


def test_content_types_is_the_first_part_and_declares_the_document() -> None:
    archive = _archive(_build())

    assert archive.infolist()[0].filename == "[Content_Types].xml"
    content_types = _parse(archive, "[Content_Types].xml")
    overrides = {
        item.get("PartName"): item.get("ContentType")
        for item in content_types.findall("ct:Override", _CT)
    }
    assert (
        overrides["/word/document.xml"]
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    )
    assert (
        overrides["/docProps/core.xml"]
        == "application/vnd.openxmlformats-package.core-properties+xml"
    )
    defaults = {
        item.get("Extension"): item.get("ContentType")
        for item in content_types.findall("ct:Default", _CT)
    }
    assert defaults["rels"] == "application/vnd.openxmlformats-package.relationships+xml"
    assert defaults["xml"] == "application/xml"


def test_package_relationships_point_at_document_and_core_properties() -> None:
    archive = _archive(_build())

    relationships = _parse(archive, "_rels/.rels")
    targets = {
        item.get("Type"): item.get("Target")
        for item in relationships.findall("r:Relationship", _REL)
    }
    assert (
        targets[
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
        ]
        == "word/document.xml"
    )
    assert (
        targets[
            "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
        ]
        == "docProps/core.xml"
    )


def test_document_preserves_text_in_reading_order() -> None:
    archive = _archive(_build())

    document = _parse(archive, "word/document.xml")

    assert _paragraphs(document) == ["鸦", 'x < y & "z"', "", "<break>", "二"]


def test_document_breaks_between_pages() -> None:
    archive = _archive(_build())

    document = _parse(archive, "word/document.xml")

    breaks = document.findall(".//w:br", _W)
    assert len(breaks) == 1
    assert breaks[0].get(f"{{{_WORD_NS}}}type") == "page"


def test_core_properties_carry_metadata() -> None:
    archive = _archive(_build())

    core = _parse(archive, "docProps/core.xml")

    assert core.find("dc:title", _CORE).text == "Scanned <Book> & Co"  # type: ignore[union-attr]
    assert core.find("dc:identifier", _CORE).text == "urn:scribeforge:test"  # type: ignore[union-attr]
    assert core.find("dc:language", _CORE).text == "zh"  # type: ignore[union-attr]
    modified = core.find("dcterms:modified", _CORE)
    assert modified is not None
    assert modified.text == "2026-10-20T12:00:00Z"
    assert modified.get(f"{{{_CORE['xsi']}}}type") == "dcterms:W3CDTF"


def test_build_is_deterministic() -> None:
    assert _build() == _build()


def test_modified_timestamp_is_normalized_to_utc() -> None:
    shifted = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    archive = _archive(_build(modified=shifted))

    core = _parse(archive, "docProps/core.xml")
    assert core.find("dcterms:modified", _CORE).text == "2026-10-20T12:00:00Z"  # type: ignore[union-attr]


def test_build_rejects_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _build(modified=datetime(2026, 10, 20, 12, 0, 0))


def test_build_rejects_an_unsafe_language_tag() -> None:
    with pytest.raises(ValueError, match="language"):
        _build(language='zh" onload="x')


def test_build_rejects_an_empty_identifier() -> None:
    with pytest.raises(ValueError, match="identifier"):
        _build(identifier="   ")


def test_core_properties_include_author_when_given() -> None:
    archive = _archive(_build(author="Ada Lovelace"))

    core = _parse(archive, "docProps/core.xml")

    assert core.find("dc:creator", _CORE).text == "Ada Lovelace"  # type: ignore[union-attr]


def test_core_properties_omit_author_when_absent() -> None:
    archive = _archive(_build())

    core = _parse(archive, "docProps/core.xml")

    assert core.find("dc:creator", _CORE) is None


def test_core_properties_escape_the_author() -> None:
    archive = _archive(_build(author="A & B <C>"))

    core = _parse(archive, "docProps/core.xml")

    assert core.find("dc:creator", _CORE).text == "A & B <C>"  # type: ignore[union-attr]


def test_build_rejects_a_book_without_pages() -> None:
    with pytest.raises(ValueError, match="no pages"):
        build_docx(
            VerifiedBook("Empty", ()),
            identifier="urn:test",
            language="zh",
            modified=_MODIFIED,
        )


def test_write_docx_replaces_the_target_atomically(tmp_path: Path) -> None:
    path = tmp_path / "out" / "book.docx"

    written = write_docx(
        _book(), path, identifier="urn:scribeforge:test", language="zh", modified=_MODIFIED
    )

    assert written == path
    assert path.read_bytes() == _build()
    assert [entry.name for entry in path.parent.iterdir()] == ["book.docx"]
