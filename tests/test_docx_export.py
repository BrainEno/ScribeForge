from __future__ import annotations

import hashlib
import io
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.docx import build_docx, write_docx
from scribeforge.domain.content import VerifiedBook, VerifiedInlineFigure, VerifiedPage

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


def _inline_book(caption: str = "A verified caption") -> tuple[VerifiedBook, bytes]:
    image_buffer = io.BytesIO()
    Image.new("RGB", (32, 16), "navy").save(image_buffer, format="PNG")
    image_bytes = image_buffer.getvalue()
    figure = VerifiedInlineFigure(
        page_index=0,
        file_name="source-crop.png",
        media_type="image/png",
        sha256=hashlib.sha256(image_bytes).hexdigest(),
        data=image_bytes,
        alt="A small diagram",
        caption=caption,
        reading_position=1,
        confirmation_id=17,
        anchor_pair_index=1,
        run_id=9,
        block_index=0,
    )
    return VerifiedBook(
        "Illustrated", (VerifiedPage(0, ("Before", "After")),), inline_figures=(figure,)
    ), image_bytes


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
        result.append("".join(token.text or "" for token in paragraph.iter(f"{{{_WORD_NS}}}t")))
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

    assert _paragraphs(document) == ['鸦x < y & "z"', "<break>", "二"]


def test_docx_embeds_confirmed_inline_figure_and_relationship_with_digest() -> None:
    book, image_bytes = _inline_book()
    data = build_docx(
        book,
        identifier="urn:scribeforge:illustrated",
        language="en",
        modified=_MODIFIED,
    )

    with _archive(data) as archive:
        names = set(archive.namelist())
        assert "word/media/inline-000001-000017.png" in names
        assert archive.read("word/media/inline-000001-000017.png") == image_bytes
        relationships = _parse(archive, "word/_rels/document.xml.rels")
        relation = relationships.find("r:Relationship", _REL)
        assert relation is not None
        assert relation.get("Target") == "media/inline-000001-000017.png"
        document = _parse(archive, "word/document.xml")
        drawing = document.find(
            ".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"
        )
        assert drawing is not None
        assert drawing.get("descr") == "A small diagram"
        assert (
            len(document.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip"))
            == 1
        )
        assert _paragraphs(document) == ["Before", "", "A verified caption", "After"]
        content_types = _parse(archive, "[Content_Types].xml")
        png_type = next(
            item
            for item in content_types.findall("ct:Default", _CT)
            if item.get("Extension") == "png"
        )
        assert png_type.get("ContentType") == "image/png"


def test_docx_inline_figure_without_caption_does_not_add_editorial_text() -> None:
    book, _image_bytes = _inline_book(caption="")

    with _archive(
        build_docx(
            book, identifier="urn:scribeforge:illustrated", language="en", modified=_MODIFIED
        )
    ) as archive:
        document = _parse(archive, "word/document.xml")
        captions = [
            paragraph
            for paragraph in document.findall("w:body/w:p", _W)
            if paragraph.find("w:pPr/w:pStyle", _W) is not None
            and paragraph.find("w:pPr/w:pStyle", _W).get(f"{{{_WORD_NS}}}val") == "Caption"
        ]
        assert captions == []


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


@pytest.mark.parametrize("policy", ["preserve", "canonical_variants", "simplified"])
def test_core_properties_declare_the_export_normalization_policy(policy: str) -> None:
    core = _parse(_archive(_build(normalization=policy)), "docProps/core.xml")

    assert core.find("dc:description", _CORE).text == (  # type: ignore[union-attr]
        f"ScribeForge normalization: {policy}"
    )


def test_core_properties_normalization_defaults_to_preserve() -> None:
    core = _parse(_archive(_build()), "docProps/core.xml")

    assert core.find("dc:description", _CORE).text == (  # type: ignore[union-attr]
        "ScribeForge normalization: preserve"
    )


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


def test_build_rejects_text_that_xml_cannot_represent() -> None:
    book = VerifiedBook("Title", (VerifiedPage(0, ("fine", "bad \x00 char")),))

    with pytest.raises(ValueError, match=r"page 0 line 1.*U\+0000"):
        build_docx(book, identifier="urn:test", language="zh", modified=_MODIFIED)


def test_build_rejects_a_title_that_xml_cannot_represent() -> None:
    book = VerifiedBook("Bad \x0b title", (VerifiedPage(0, ("ok",)),))

    with pytest.raises(ValueError, match=r"title.*U\+000B"):
        build_docx(book, identifier="urn:test", language="zh", modified=_MODIFIED)


def test_build_rejects_an_author_that_xml_cannot_represent() -> None:
    with pytest.raises(ValueError, match=r"author.*U\+001F"):
        _build(author="bad \x1f author")


def test_build_accepts_xml_whitespace_characters() -> None:
    book = VerifiedBook("Title", (VerifiedPage(0, ("a\tb", "c\nd")),))

    archive = _archive(build_docx(book, identifier="urn:test", language="zh", modified=_MODIFIED))

    document = _parse(archive, "word/document.xml")
    assert _paragraphs(document) == ["a\tb c\nd"]


def test_build_docx_rechecks_the_rendered_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    checked: list[int] = []
    monkeypatch.setattr(
        "scribeforge.adapters.docx.check_docx",
        lambda data, book: checked.append(len(data)),
    )

    data = _build()

    assert checked == [len(data)]


def test_write_docx_replaces_the_target_atomically(tmp_path: Path) -> None:
    path = tmp_path / "out" / "book.docx"

    written = write_docx(
        _book(), path, identifier="urn:scribeforge:test", language="zh", modified=_MODIFIED
    )

    assert written == path
    assert path.read_bytes() == _build()
    assert [entry.name for entry in path.parent.iterdir()] == ["book.docx"]
