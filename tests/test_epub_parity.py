"""#72 structural parity between the trusted and candidate EPUB exporters.

The trusted EPUB (verified decisions only) and the candidate EPUB (raw OCR for
A/B comparison) are deliberately separate layers, but their *container
skeleton* must stay identical: same mimetype handling, byte-identical
container.xml, same OPF/nav shape. Only the page content may differ. These
tests pin that contract so a format fix cannot silently drift to one side.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from xml.etree import ElementTree

import pytest

from scribeforge.adapters.candidate_epub import build_candidate_epub
from scribeforge.adapters.epub import build_epub
from scribeforge.adapters.export_common import container_xml
from scribeforge.domain.candidate import OcrCandidateBook, OcrCandidatePage
from scribeforge.domain.content import VerifiedBook, VerifiedPage

_MODIFIED = datetime(2026, 10, 20, 12, 0, 0, tzinfo=UTC)
_OPF_NS = {"o": "http://www.idpf.org/2007/opf"}


def _trusted() -> bytes:
    book = VerifiedBook(
        "Book",
        (VerifiedPage(0, ("鸦", "verified")), VerifiedPage(1, ("二",))),
    )
    return build_epub(book, identifier="urn:scribeforge:parity", language="zh", modified=_MODIFIED)


def _candidate() -> bytes:
    book = OcrCandidateBook(
        source_title="Book",
        engine="mineru",
        pages=(
            OcrCandidatePage(0, 1, "4.0.1", ("raw one",)),
            OcrCandidatePage(1, 2, "4.0.1", ("raw two",)),
        ),
    )
    return build_candidate_epub(
        book, identifier="urn:scribeforge:parity:candidate", language="zh", modified=_MODIFIED
    )


def test_container_xml_is_shared_not_duplicated() -> None:
    # Both export layers must build on the single container implementation in
    # export_common, reached through the one shared package builder.
    from scribeforge.adapters import epub_builder

    assert epub_builder.container_xml is container_xml


def test_both_public_builders_route_through_one_shared_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # #72 downgrade-mode refactor: the trusted and candidate exporters must be
    # the same builder in two modes, not two parallel implementations.
    from scribeforge.adapters import epub_builder

    calls: list[dict[str, object]] = []
    real_builder = epub_builder.build_epub_package

    def spy(**kwargs: object) -> bytes:
        calls.append(kwargs)
        return real_builder(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(epub_builder, "build_epub_package", spy)
    _trusted()
    _candidate()

    assert len(calls) == 2
    modes = [call["unverified"] for call in calls]
    assert modes == [False, True]


def test_shared_builder_enforces_the_unverified_marker_both_ways() -> None:
    # Fail closed in both directions: an unverified package must carry the
    # warning, and a trusted package must never contain it.
    from scribeforge.adapters import epub_builder

    document = epub_builder.EpubDocument(
        item_id="page-0",
        file_name="page-000001.xhtml",
        xhtml=(
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head>'
            "<body><p>line</p></body></html>\n"
        ),
        nav_label="Page 1",
    )
    base = {
        "identifier": "urn:scribeforge:parity:guard",
        "language": "zh",
        "modified": "2026-10-20T12:00:00Z",
        "documents": [document],
    }
    with pytest.raises(ValueError, match="UNVERIFIED"):
        epub_builder.build_epub_package(title="Book", unverified=True, **base)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="UNVERIFIED"):
        epub_builder.build_epub_package(
            title=f"Book — {epub_builder.UNVERIFIED_CANDIDATE_WARNING}",
            unverified=False,
            **base,  # type: ignore[arg-type]
        )
    # A correctly marked candidate passes the guard.
    data = epub_builder.build_epub_package(
        title=f"Book — {epub_builder.UNVERIFIED_CANDIDATE_WARNING}",
        description=epub_builder.UNVERIFIED_CANDIDATE_WARNING,
        unverified=True,
        **base,  # type: ignore[arg-type]
    )
    assert b"UNVERIFIED" in zipfile.ZipFile(io.BytesIO(data)).read("OEBPS/content.opf")


def test_both_exporters_write_identical_container_skeleton() -> None:
    trusted = zipfile.ZipFile(io.BytesIO(_trusted()))
    candidate = zipfile.ZipFile(io.BytesIO(_candidate()))
    try:
        for archive in (trusted, candidate):
            info = archive.infolist()[0]
            assert info.filename == "mimetype"
            assert info.compress_type == zipfile.ZIP_STORED
            assert archive.read("mimetype") == b"application/epub+zip"
        assert trusted.read("META-INF/container.xml") == candidate.read("META-INF/container.xml")
        assert trusted.read("META-INF/container.xml") == container_xml().encode("utf-8")
        for name in ("OEBPS/content.opf", "OEBPS/nav.xhtml"):
            assert name in trusted.namelist()
            assert name in candidate.namelist()
    finally:
        trusted.close()
        candidate.close()


def test_opf_and_nav_have_the_same_structure_only_content_differs() -> None:
    trusted = zipfile.ZipFile(io.BytesIO(_trusted()))
    candidate = zipfile.ZipFile(io.BytesIO(_candidate()))
    try:
        for archive in (trusted, candidate):
            package = ElementTree.fromstring(archive.read("OEBPS/content.opf"))
            manifest = package.find("o:manifest", _OPF_NS)
            spine = package.find("o:spine", _OPF_NS)
            assert manifest is not None and spine is not None
            item_ids = [item.get("id") for item in manifest.findall("o:item", _OPF_NS)]
            itemrefs = [ref.get("idref") for ref in spine.findall("o:itemref", _OPF_NS)]
            # nav document plus one item per page; the spine covers every
            # content document (the nav itself is referenced via @toc).
            nav_ids = [item_id for item_id in item_ids if "nav" in (item_id or "")]
            assert len(nav_ids) == 1
            assert sorted(itemrefs) == sorted(
                item_id for item_id in item_ids if item_id not in nav_ids
            )
            nav = ElementTree.fromstring(archive.read("OEBPS/nav.xhtml"))
            nav_text = "".join(nav.itertext())
            assert "Contents" in nav_text  # same nav skeleton in both exporters
        # Content must differ: trusted carries verified text, candidate raw.
        trusted_pages = [
            n
            for n in trusted.namelist()
            if n.startswith("OEBPS/") and n.endswith(".xhtml") and "nav" not in n
        ]
        candidate_pages = [
            n
            for n in candidate.namelist()
            if n.startswith("OEBPS/") and n.endswith(".xhtml") and "nav" not in n
        ]
        assert len(trusted_pages) == len(candidate_pages) == 2
        trusted_text = "".join(
            "".join(ElementTree.fromstring(trusted.read(name)).itertext()) for name in trusted_pages
        )
        candidate_text = "".join(
            "".join(ElementTree.fromstring(candidate.read(name)).itertext())
            for name in candidate_pages
        )
        assert "verified" in trusted_text
        assert "raw one" in candidate_text
        assert "UNVERIFIED" in "".join(
            ElementTree.fromstring(candidate.read("OEBPS/content.opf")).itertext()
        )
        assert "UNVERIFIED" not in "".join(
            ElementTree.fromstring(trusted.read("OEBPS/content.opf")).itertext()
        )
    finally:
        trusted.close()
        candidate.close()
