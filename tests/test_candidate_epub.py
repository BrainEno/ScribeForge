from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from scribeforge.adapters.candidate_epub import build_candidate_epub
from scribeforge.domain.candidate import OcrCandidateBook, OcrCandidatePage


def _candidate(lines: tuple[str, ...] = ("raw <text>", "not proofread")) -> OcrCandidateBook:
    return OcrCandidateBook(
        source_title="Book",
        engine="mineru",
        pages=(
            OcrCandidatePage(0, 7, "4.0.1", lines),
            OcrCandidatePage(1, 8, "4.0.1", ("第二页",)),
        ),
    )


def test_candidate_epub_is_valid_and_clearly_marked_unverified() -> None:
    candidate = _candidate()
    data = build_candidate_epub(
        candidate,
        identifier="urn:scribeforge:candidate:abc:mineru",
        language="zh",
        modified=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with ZipFile(BytesIO(data)) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.read("mimetype") == b"application/epub+zip"
        package = ElementTree.fromstring(archive.read("OEBPS/content.opf"))
        package_text = "".join(package.itertext())
        assert "UNVERIFIED OCR CANDIDATE" in package_text
        assert "mineru" in package_text
        first_page = ElementTree.fromstring(archive.read("OEBPS/page-000001.xhtml"))
        ns = {"x": "http://www.w3.org/1999/xhtml"}
        assert [node.text for node in first_page.findall(".//x:p", ns)] == [
            "raw <text>",
            "not proofread",
        ]
        second_page = ElementTree.fromstring(archive.read("OEBPS/page-000002.xhtml"))
        assert [node.text for node in second_page.findall(".//x:p", ns)] == ["第二页"]


def test_candidate_epub_rejects_duplicate_or_unordered_page_indexes() -> None:
    duplicate = OcrCandidateBook(
        "Book",
        "mineru",
        (
            OcrCandidatePage(0, 1, "v1", ("a",)),
            OcrCandidatePage(0, 2, "v1", ("b",)),
        ),
    )
    with pytest.raises(ValueError, match="unique"):
        build_candidate_epub(
            duplicate,
            identifier="urn:scribeforge:candidate:abc:mineru",
            language="en",
            modified=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_candidate_epub_rejects_xml_control_characters() -> None:
    with pytest.raises(ValueError, match="XML cannot represent"):
        build_candidate_epub(
            _candidate(("bad \x0b text",)),
            identifier="urn:scribeforge:candidate:abc:mineru",
            language="en",
            modified=datetime(2026, 1, 1, tzinfo=UTC),
        )
