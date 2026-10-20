from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime

import pytest

from scribeforge.adapters.docx import build_docx
from scribeforge.adapters.epub import build_epub
from scribeforge.adapters.export_check import (
    ExportIntegrityError,
    check_docx,
    check_epub,
)
from scribeforge.domain.content import VerifiedBook, VerifiedPage

_MODIFIED = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
_EPUB_OPTIONS: dict[str, object] = {
    "identifier": "urn:scribeforge:test",
    "language": "zh",
    "modified": _MODIFIED,
}
_DOCX_OPTIONS: dict[str, object] = dict(_EPUB_OPTIONS)


def _book() -> VerifiedBook:
    return VerifiedBook(
        "Scanned Book",
        (VerifiedPage(0, ("one", "two")), VerifiedPage(1, ("three",))),
    )


def _epub(book: VerifiedBook | None = None) -> bytes:
    return build_epub(book or _book(), **_EPUB_OPTIONS)  # type: ignore[arg-type]


def _docx(book: VerifiedBook | None = None) -> bytes:
    return build_docx(book or _book(), **_DOCX_OPTIONS)  # type: ignore[arg-type]


def _rewrite(
    data: bytes,
    *,
    replace: dict[str, bytes] | None = None,
    drop: frozenset[str] = frozenset(),
    add: dict[str, bytes] | None = None,
    compress: dict[str, int] | None = None,
) -> bytes:
    source = zipfile.ZipFile(io.BytesIO(data))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            name = info.filename
            if name in drop:
                continue
            payload = source.read(name)
            if replace is not None and name in replace:
                payload = replace[name]
            if compress is not None and name in compress:
                info.compress_type = compress[name]
            target.writestr(info, payload)
        for name, payload in (add or {}).items():
            target.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), payload)
    return buffer.getvalue()


def _entry(data: bytes, name: str) -> str:
    return zipfile.ZipFile(io.BytesIO(data)).read(name).decode("utf-8")


def _corrupt_stored_entry(data: bytes, name: str) -> bytes:
    archive = zipfile.ZipFile(io.BytesIO(data))
    info = archive.getinfo(name)
    assert info.compress_type == zipfile.ZIP_STORED
    start = info.header_offset + 30 + len(info.filename.encode("utf-8")) + len(info.extra)
    raw = bytearray(data)
    raw[start] ^= 0xFF
    return bytes(raw)


# --- EPUB -------------------------------------------------------------------


def test_check_epub_accepts_a_well_formed_archive() -> None:
    check_epub(_epub(), _book())


def test_check_epub_accepts_an_untitled_book() -> None:
    book = VerifiedBook("", (VerifiedPage(0, ("one",)),))

    check_epub(_epub(book), book)


def test_check_epub_rejects_a_corrupt_entry() -> None:
    with pytest.raises(ExportIntegrityError, match="corrupt archive entry"):
        check_epub(_corrupt_stored_entry(_epub(), "mimetype"), _book())


def test_check_epub_rejects_altered_page_text() -> None:
    data = _rewrite(
        _epub(),
        replace={
            "OEBPS/page-000001.xhtml": (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<html xmlns="http://www.w3.org/1999/xhtml"><body><section>'
                b"<p>one</p><p>changed</p></section></body></html>"
            )
        },
    )

    with pytest.raises(ExportIntegrityError, match="page 0 text"):
        check_epub(data, _book())


def test_check_epub_rejects_a_book_that_does_not_match_the_archive() -> None:
    book = VerifiedBook(
        "Scanned Book",
        (VerifiedPage(0, ("one", "two", "extra")), VerifiedPage(1, ("three",))),
    )

    with pytest.raises(ExportIntegrityError, match="page 0 text"):
        check_epub(_epub(), book)


def test_check_epub_rejects_a_non_zip_payload() -> None:
    with pytest.raises(ExportIntegrityError, match="zip"):
        check_epub(b"not a zip archive", _book())


def test_check_epub_rejects_a_missing_page_entry() -> None:
    data = _rewrite(_epub(), drop=frozenset({"OEBPS/page-000001.xhtml"}))

    with pytest.raises(ExportIntegrityError, match="manifest item 'page-0'"):
        check_epub(data, _book())


def test_check_epub_rejects_a_missing_mimetype() -> None:
    data = _rewrite(_epub(), drop=frozenset({"mimetype"}))

    with pytest.raises(ExportIntegrityError, match="mimetype"):
        check_epub(data, _book())


def test_check_epub_rejects_a_compressed_mimetype() -> None:
    data = _rewrite(_epub(), compress={"mimetype": zipfile.ZIP_DEFLATED})

    with pytest.raises(ExportIntegrityError, match="mimetype"):
        check_epub(data, _book())


def test_check_epub_rejects_a_wrong_mimetype() -> None:
    data = _rewrite(_epub(), replace={"mimetype": b"text/plain"})

    with pytest.raises(ExportIntegrityError, match="mimetype"):
        check_epub(data, _book())


def test_check_epub_rejects_a_missing_container() -> None:
    data = _rewrite(_epub(), drop=frozenset({"META-INF/container.xml"}))

    with pytest.raises(ExportIntegrityError, match="container"):
        check_epub(data, _book())


def test_check_epub_rejects_a_container_without_rootfile() -> None:
    data = _rewrite(_epub(), replace={"META-INF/container.xml": b"<container/>"})

    with pytest.raises(ExportIntegrityError, match="rootfile"):
        check_epub(data, _book())


def test_check_epub_rejects_a_missing_package_document() -> None:
    data = _rewrite(_epub(), drop=frozenset({"OEBPS/content.opf"}))

    with pytest.raises(ExportIntegrityError, match="package document"):
        check_epub(data, _book())


def test_check_epub_rejects_malformed_xml() -> None:
    data = _rewrite(_epub(), replace={"OEBPS/nav.xhtml": b"<html>"})

    with pytest.raises(ExportIntegrityError, match="well-formed"):
        check_epub(data, _book())


def test_check_epub_rejects_out_of_order_pages() -> None:
    original = _entry(_epub(), "OEBPS/content.opf")
    swapped = original.replace(
        '<itemref idref="page-0"/><itemref idref="page-1"/>',
        '<itemref idref="page-1"/><itemref idref="page-0"/>',
    )
    assert swapped != original

    data = _rewrite(_epub(), replace={"OEBPS/content.opf": swapped.encode("utf-8")})

    with pytest.raises(ExportIntegrityError, match="spine"):
        check_epub(data, _book())


def test_check_epub_rejects_a_package_title_mismatch() -> None:
    original = _entry(_epub(), "OEBPS/content.opf")
    tampered = original.replace("<dc:title>Scanned Book</dc:title>", "<dc:title>Other</dc:title>")
    assert tampered != original

    data = _rewrite(_epub(), replace={"OEBPS/content.opf": tampered.encode("utf-8")})

    with pytest.raises(ExportIntegrityError, match="title"):
        check_epub(data, _book())


# --- DOCX -------------------------------------------------------------------


def test_check_docx_accepts_a_well_formed_archive() -> None:
    check_docx(_docx(), _book())


def test_check_docx_rejects_altered_page_text() -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>one</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>changed</w:t></w:r></w:p>"
        '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
        "<w:p><w:r><w:t>three</w:t></w:r></w:p>"
        "<w:sectPr/></w:body></w:document>"
    )

    data = _rewrite(_docx(), replace={"word/document.xml": document.encode("utf-8")})

    with pytest.raises(ExportIntegrityError, match="page 0 text"):
        check_docx(data, _book())


def test_check_docx_rejects_a_missing_page_break() -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(
            f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>"
            for line in ("one", "two", "three")
        )
        + "<w:sectPr/></w:body></w:document>"
    )

    data = _rewrite(_docx(), replace={"word/document.xml": document.encode("utf-8")})

    with pytest.raises(ExportIntegrityError, match="document has 1 page"):
        check_docx(data, _book())


def test_check_docx_rejects_a_missing_part_declared_by_content_types() -> None:
    data = _rewrite(_docx(), drop=frozenset({"docProps/core.xml"}))

    with pytest.raises(ExportIntegrityError, match="missing part"):
        check_docx(data, _book())


def test_check_docx_rejects_a_part_without_a_content_type() -> None:
    data = _rewrite(_docx(), add={"extra/payload.bin": b"x"})

    with pytest.raises(ExportIntegrityError, match="content type"):
        check_docx(data, _book())


def test_check_docx_rejects_a_broken_relationship() -> None:
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="officeDocument" Target="word/missing.xml"/>'
        "</Relationships>"
    )

    data = _rewrite(_docx(), replace={"_rels/.rels": relationships.encode("utf-8")})

    with pytest.raises(ExportIntegrityError, match="relationship"):
        check_docx(data, _book())


def test_check_docx_rejects_malformed_xml() -> None:
    data = _rewrite(_docx(), replace={"docProps/core.xml": b"<core>"})

    with pytest.raises(ExportIntegrityError, match="well-formed"):
        check_docx(data, _book())


def test_check_docx_rejects_a_content_types_part_that_is_not_first() -> None:
    data = _rewrite(_docx(), drop=frozenset({"[Content_Types].xml"}))

    with pytest.raises(ExportIntegrityError, match="Content_Types"):
        check_docx(data, _book())


def test_check_docx_rejects_a_core_title_mismatch() -> None:
    original = _entry(_docx(), "docProps/core.xml")
    tampered = original.replace("<dc:title>Scanned Book</dc:title>", "<dc:title>Other</dc:title>")
    assert tampered != original

    data = _rewrite(_docx(), replace={"docProps/core.xml": tampered.encode("utf-8")})

    with pytest.raises(ExportIntegrityError, match="title"):
        check_docx(data, _book())
