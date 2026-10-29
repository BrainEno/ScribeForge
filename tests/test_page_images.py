"""Pages that are pictures: the cover and the plates.

A reflowed book carries everything except what is *drawn*. These tests pin both
halves of that claim: only a page with no accepted text becomes an image (and only
with a recorded reason), and the image that reaches the reader is byte-for-byte the
raster the evidence points at.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.epub import build_epub
from scribeforge.adapters.export_check import ExportIntegrityError, check_epub
from scribeforge.adapters.pdf_pages import PdfPageSource
from scribeforge.domain.alignment import LineAlignment, PageAlignment
from scribeforge.domain.content import (
    PageImageRole,
    VerifiedBook,
    VerifiedChapter,
    VerifiedImage,
    VerifiedPage,
)
from scribeforge.domain.flow import build_flow
from scribeforge.domain.ocr import BoundingBox, OCRLine, PageOCRResult
from scribeforge.domain.risk import PairRisk
from scribeforge.domain.structure import (
    NumberingScheme,
    PageLabel,
    PageRole,
    PageStructure,
)
from scribeforge.pipeline.assembly import PageImageError, assemble_verified_content
from scribeforge.pipeline.structure import build_book_structure, persist_book_structure
from scribeforge.storage.sqlite import SQLiteStore

MODIFIED = datetime(2024, 1, 1, tzinfo=UTC)


def _png(path: Path, colour: str = "white") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 60), colour).save(path)
    return path


def _store(tmp_path: Path) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    return store, store.create_project("V.", "V..pdf", "sha256")


def _add_page(
    store: SQLiteStore,
    project_id: int,
    page_index: int,
    texts: tuple[str, ...],
    *,
    raster: Path,
    undecided: frozenset[int] = frozenset(),
) -> None:
    store.add_page(project_id, page_index, str(raster), 40, 60)
    lines = tuple(
        OCRLine(text, BoundingBox(0.1, 0.1 + 0.1 * index, 0.5, 0.05)) for index, text in enumerate(texts)
    )
    primary = store.record_ocr(project_id, PageOCRResult(page_index, "primary", "1.0", lines))
    secondary = store.record_ocr(project_id, PageOCRResult(page_index, "secondary", "1.0", lines))
    alignment = PageAlignment(
        page_index,
        "primary",
        "secondary",
        tuple(LineAlignment(index, index, 1.0, 0.0, ()) for index in range(len(texts))),
    )
    risks = tuple(PairRisk(index, 0.0, ()) for index in range(len(texts)))
    store.record_verification(primary, secondary, alignment, risks, ())
    for index, text in enumerate(texts):
        if index in undecided:
            continue
        store.append_decision(
            project_id=project_id,
            page_index=page_index,
            pair_index=index,
            selected_text=text,
            actor="automatic",
            evidence_refs=(),
        )


def _confirm(store: SQLiteStore, project_id: int, page_index: int, kind: str) -> None:
    store.record_page_confirmation(
        project_id=project_id,
        page_index=page_index,
        kind=kind,
        actor="human",
        reason="checked the scan",
    )


def _roles(store: SQLiteStore, project_id: int, roles: dict[int, PageRole]) -> None:
    store.record_page_structure(
        project_id,
        tuple(PageStructure(index, role) for index, role in sorted(roles.items())),
    )


# ------------------------------------------------------------------ assembly


def test_a_confirmed_cover_becomes_the_cover_image(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    cover = _png(tmp_path / "pages" / "0.png", "black")
    _add_page(store, project_id, 0, (), raster=cover)
    _add_page(store, project_id, 1, ("正文。",), raster=_png(tmp_path / "pages" / "1.png"))
    _confirm(store, project_id, 0, "non_text")
    _roles(store, project_id, {0: PageRole.COVER, 1: PageRole.BODY})

    book = assemble_verified_content(store, project_id)

    assert [image.page_index for image in book.images] == [0]
    image = book.images[0]
    assert image.role is PageImageRole.COVER
    assert image.media_type == "image/png"
    assert image.data == cover.read_bytes()
    assert image.sha256 == hashlib.sha256(cover.read_bytes()).hexdigest()
    assert image.alt == "封面"
    assert book.cover_image == image


def test_a_plate_is_placed_as_a_figure_with_its_printed_page(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    plate = _png(tmp_path / "pages" / "7.png", "gray")
    _add_page(store, project_id, 7, (), raster=plate)
    _add_page(store, project_id, 8, ("正文。",), raster=_png(tmp_path / "pages" / "8.png"))
    _confirm(store, project_id, 7, "non_text")
    store.record_page_structure(
        project_id,
        (
            PageStructure(7, PageRole.PLATE, label=PageLabel("117", NumberingScheme.ARABIC, 117)),
            PageStructure(8, PageRole.BODY),
        ),
    )

    book = assemble_verified_content(store, project_id)

    assert len(book.images) == 1
    assert book.images[0].role is PageImageRole.FIGURE
    assert book.images[0].alt == "第 117 页的插图"
    assert book.cover_image is None


def test_a_non_text_confirmation_makes_an_image_even_without_a_role(tmp_path: Path) -> None:
    """The person looked at the page; the role detector may have said "body"."""

    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 3, (), raster=_png(tmp_path / "pages" / "3.png"))
    _add_page(store, project_id, 4, ("正文。",), raster=_png(tmp_path / "pages" / "4.png"))
    _confirm(store, project_id, 3, "non_text")

    book = assemble_verified_content(store, project_id)

    assert [image.role for image in book.images] == [PageImageRole.FIGURE]


def test_a_measured_plate_is_embedded_without_a_confirmation(tmp_path: Path) -> None:
    """The structure pass measured the page; nobody has to confirm it again.

    This is the case of a plate page whose readers returned one *empty* line each:
    the page exports (there is no unread text left open), so the rasters are the
    only thing that can carry the illustration.
    """

    store, project_id = _store(tmp_path)
    _add_page(
        store,
        project_id,
        5,
        ("",),
        raster=_png(tmp_path / "pages" / "5.png"),
        undecided=frozenset({0}),
    )
    _add_page(store, project_id, 6, ("正文。",), raster=_png(tmp_path / "pages" / "6.png"))
    store.record_page_structure(
        project_id,
        (PageStructure(5, PageRole.PLATE), PageStructure(6, PageRole.BODY)),
    )

    book = assemble_verified_content(store, project_id)

    assert [image.page_index for image in book.images] == [5]
    assert book.images[0].role is PageImageRole.FIGURE


def test_a_confirmed_blank_leaf_is_not_embedded(tmp_path: Path) -> None:
    """There is nothing to look at: a white rectangle in the flow is noise."""

    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, (), raster=_png(tmp_path / "pages" / "0.png"))
    _add_page(store, project_id, 1, ("正文。",), raster=_png(tmp_path / "pages" / "1.png"))
    _confirm(store, project_id, 0, "blank")
    _roles(store, project_id, {0: PageRole.BLANK, 1: PageRole.BODY})

    book = assemble_verified_content(store, project_id)

    assert book.images == ()
    assert book.confirmed_blank_pages == (0,)


def test_a_page_with_text_is_never_mirrored(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, ("正文。",), raster=_png(tmp_path / "pages" / "0.png"))
    _confirm(store, project_id, 0, "non_text")

    book = assemble_verified_content(store, project_id)

    assert book.images == ()


def test_a_missing_raster_refuses_instead_of_dropping_the_image(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    _add_page(store, project_id, 0, (), raster=tmp_path / "pages" / "gone.png")
    _add_page(store, project_id, 1, ("正文。",), raster=_png(tmp_path / "pages" / "1.png"))
    _confirm(store, project_id, 0, "non_text")

    with pytest.raises(PageImageError, match="page 0.*unreadable"):
        assemble_verified_content(store, project_id)


def test_an_unshowable_raster_format_refuses_by_name(tmp_path: Path) -> None:
    store, project_id = _store(tmp_path)
    odd = tmp_path / "pages" / "0.webp"
    odd.parent.mkdir(parents=True, exist_ok=True)
    odd.write_bytes(b"RIFF....WEBP")
    _add_page(store, project_id, 0, (), raster=odd)
    _add_page(store, project_id, 1, ("正文。",), raster=_png(tmp_path / "pages" / "1.png"))
    _confirm(store, project_id, 0, "non_text")

    with pytest.raises(PageImageError, match=".webp.*not a format"):
        assemble_verified_content(store, project_id)


# ------------------------------------------------------------------ the model


def _image(page_index: int, data: bytes, role: PageImageRole) -> VerifiedImage:
    return VerifiedImage(
        page_index=page_index,
        file_name=f"page-{page_index + 1:06d}.png",
        media_type="image/png",
        role=role,
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        alt="封面" if role is PageImageRole.COVER else "第 3 页的插图",
    )


_PNG = _png(Path("/tmp/scribeforge-test-pixel.png"), "black").read_bytes()


def _book_with(images: tuple[VerifiedImage, ...], *, pages: int = 4) -> VerifiedBook:
    """A small book whose image pages carry no text, as assembly guarantees."""

    image_pages = {image.page_index for image in images}
    return VerifiedBook(
        "V.",
        tuple(
            VerifiedPage(index, () if index in image_pages else ("正文。",))
            for index in range(pages)
        ),
        chapters=(VerifiedChapter("第一章", 1, 1),),
        images=images,
    )


def test_an_image_that_does_not_match_its_digest_is_refused() -> None:
    with pytest.raises(ValueError, match="does not match the bytes"):
        VerifiedImage(0, "page-000001.png", "image/png", PageImageRole.COVER, "0" * 64, _PNG)


def test_a_book_refuses_text_and_an_image_on_the_same_page() -> None:
    image = _image(1, _PNG, PageImageRole.FIGURE)

    with pytest.raises(ValueError, match="both accepted text and an image"):
        VerifiedBook("V.", (VerifiedPage(1, ("正文。",)),), images=(image,))


def test_a_book_refuses_an_image_for_a_missing_page() -> None:
    with pytest.raises(ValueError, match="not part of the book"):
        VerifiedBook("V.", (VerifiedPage(0, ()),), images=(_image(5, _PNG, PageImageRole.FIGURE),))


# --------------------------------------------------------------------- flow


def test_the_cover_lands_in_the_front_matter_document() -> None:
    cover = _image(0, _PNG, PageImageRole.COVER)
    book = VerifiedBook(
        "V.",
        (VerifiedPage(0, ()), VerifiedPage(1, ("正文。",))),
        chapters=(VerifiedChapter("第一章", 1, 1),),
        images=(cover,),
    )

    flow = build_flow(book)

    assert flow.documents[0].title == "Front matter"
    assert flow.documents[0].images == (cover,)
    assert flow.documents[1].images == ()


def test_a_plate_stays_with_the_chapter_whose_page_it_is() -> None:
    plate = _image(2, _PNG, PageImageRole.FIGURE)
    book = VerifiedBook(
        "V.",
        (VerifiedPage(1, ("第一章正文。",)), VerifiedPage(2, ()), VerifiedPage(3, ("第二章正文。",))),
        chapters=(VerifiedChapter("第一章", 1, 1), VerifiedChapter("第二章", 1, 3)),
        images=(plate,),
    )

    flow = build_flow(book)

    assert [document.title for document in flow.documents] == ["第一章", "第二章"]
    assert flow.documents[0].images == (plate,)
    assert flow.documents[1].images == ()


# --------------------------------------------------------------------- epub


def _epub(book: VerifiedBook, *, layout: str = "auto") -> bytes:
    return build_epub(
        book,
        identifier="urn:scribeforge:test",
        language="zh",
        modified=MODIFIED,
        layout=layout,
    )


def _rewrite(data: bytes, name: str, replacement: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            target.writestr(info, replacement if info.filename == name else payload)
    return buffer.getvalue()


def test_epub_embeds_the_cover_with_both_declarations() -> None:
    cover = _image(0, _PNG, PageImageRole.COVER)
    book = _book_with((cover,))

    data = _epub(book)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.read("OEBPS/images/page-000001.png") == _PNG
        package = archive.read("OEBPS/content.opf").decode("utf-8")
        # A one-page book has no chapter tree, so the facsimile layout applies.
        front = archive.read("OEBPS/doc-000001.xhtml").decode("utf-8")
    assert 'properties="cover-image"' in package
    assert '<meta name="cover" content="image-page-0"/>' in package
    assert '<img src="images/page-000001.png" alt="封面"/>' in front
    # The cover is the first thing in the reading order, after its page marker.
    assert front.index('id="page-0"') < front.index("<figure")


def test_epub_puts_a_plate_behind_its_page_break() -> None:
    plate = _image(2, _PNG, PageImageRole.FIGURE)
    book = _book_with((plate,))

    data = _epub(book)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        # Page 0 is front matter; the plate sits in the chapter that owns page 2.
        document = archive.read("OEBPS/doc-000002.xhtml").decode("utf-8")
    start = document.index('id="page-2"')
    shown = document.index('<img src="images/page-000003.png"')
    assert start < shown < document.index('id="page-3"')


def test_epub_page_layout_keeps_an_image_in_its_own_document() -> None:
    plate = _image(2, _PNG, PageImageRole.FIGURE)
    book = _book_with((plate,))

    data = _epub(book, layout="page")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        assert "OEBPS/page-000003.xhtml" in names
        owning = [
            name
            for name in names
            if name.endswith(".xhtml") and b"page-000003.png" in archive.read(name)
        ]
    assert owning == ["OEBPS/page-000003.xhtml"]


def test_epub_renders_a_plate_caption_as_figcaption() -> None:
    """A plate page whose single OCR line is a caption renders <figcaption>."""

    image = VerifiedImage(
        page_index=2,
        file_name="page-000003.png",
        media_type="image/png",
        role=PageImageRole.FIGURE,
        sha256=hashlib.sha256(_PNG).hexdigest(),
        data=_PNG,
        alt="第 3 页的插图",
        caption="远手点：溜溜球离开操纵它的手距离最远时的那一点。",
    )
    book = _book_with((image,))

    data = _epub(book)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("OEBPS/doc-000002.xhtml").decode("utf-8")

    assert "<figcaption>" in document
    assert "远手点：溜溜球离开操纵它的手距离最远时的那一点。" in document
    assert "</figcaption>" in document


def test_epub_without_images_declares_no_cover() -> None:
    data = _epub(_book_with(()))

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        package = archive.read("OEBPS/content.opf").decode("utf-8")
        assert not [name for name in archive.namelist() if "/images/" in name]
    assert "cover-image" not in package
    assert 'name="cover"' not in package


# ------------------------------------------------------------- the self-check


def test_a_tampered_image_is_rejected() -> None:
    book = _book_with((_image(0, _PNG, PageImageRole.COVER),))
    data = _epub(book)

    tampered = _rewrite(data, "OEBPS/images/page-000001.png", _PNG + b"\x00")

    with pytest.raises(ExportIntegrityError, match="bytes differ"):
        check_epub(tampered, book)


def test_a_document_that_hides_its_image_is_rejected() -> None:
    book = _book_with((_image(0, _PNG, PageImageRole.COVER),))
    data = _epub(book)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("OEBPS/doc-000001.xhtml")
    stripped = re.sub(rb"<figure.*?</figure>", b"", document, flags=re.DOTALL)

    with pytest.raises(ExportIntegrityError, match="does not show exactly its own images"):
        check_epub(_rewrite(data, "OEBPS/doc-000001.xhtml", stripped), book)


def test_an_image_without_alternative_text_is_rejected() -> None:
    book = _book_with((_image(0, _PNG, PageImageRole.COVER),))
    data = _epub(book)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("OEBPS/doc-000001.xhtml")
    stripped = document.replace('alt="封面"'.encode(), b'alt=""')

    with pytest.raises(ExportIntegrityError, match="no alternative text"):
        check_epub(_rewrite(data, "OEBPS/doc-000001.xhtml", stripped), book)


def test_an_image_the_book_does_not_claim_is_rejected() -> None:
    book = _book_with(())
    data = _epub(book)

    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            target.writestr(info, source.read(info.filename))
        target.writestr(zipfile.ZipInfo("OEBPS/images/stray.png"), _PNG)

    with pytest.raises(ExportIntegrityError, match="does not claim"):
        check_epub(buffer.getvalue(), book)


def test_the_cover_must_be_declared_when_the_book_has_one() -> None:
    book = _book_with((_image(0, _PNG, PageImageRole.COVER),))
    data = _epub(book)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        package = archive.read("OEBPS/content.opf").decode("utf-8")
    stripped = package.replace(' properties="cover-image"', "").encode()

    with pytest.raises(ExportIntegrityError, match="cover image"):
        check_epub(_rewrite(data, "OEBPS/content.opf", stripped), book)


# ---------------------------------------------------------------- real scan

#: The book this project exists for. Its cover has no OCR text at all (94% of the
#: page is artwork), which is exactly the case that used to vanish from an export.
BENCHMARK_PDF = Path(__file__).resolve().parent.parent / "benchmark" / "V..pdf"


@pytest.mark.benchmark
def test_the_real_cover_of_the_benchmark_scan_becomes_the_epub_cover(
    tmp_path: Path,
) -> None:
    """End-to-end on the book's own first page, rendered by the pipeline's rasterizer.

    Nothing here is synthesised: the PNG is page 1 of `benchmark/V..pdf`, the ink
    ratio is measured from it, the role comes from the structure pass, and the
    bytes compared below are the bytes that were written into the EPUB.
    """

    if not BENCHMARK_PDF.is_file():
        pytest.skip("benchmark PDF is not available")

    rendered = PdfPageSource(BENCHMARK_PDF).render_page(0)
    raster = tmp_path / "assets" / "page-000000.png"
    raster.parent.mkdir(parents=True, exist_ok=True)
    raster.write_bytes(rendered.image_bytes)

    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", str(BENCHMARK_PDF), "sha256")
    store.add_page(project_id, 0, str(raster), rendered.width, rendered.height)
    primary = store.record_ocr(project_id, PageOCRResult(0, "primary", "1.0", ()))
    secondary = store.record_ocr(project_id, PageOCRResult(0, "secondary", "1.0", ()))
    store.record_verification(
        primary,
        secondary,
        PageAlignment(0, "primary", "secondary", ()),
        (),
        (),
    )
    structure = build_book_structure(store, project_id, engine="primary")
    roles = {entry.page_index: entry.role for entry in structure.pages}
    ink = {entry.page_index: entry.ink_ratio for entry in structure.pages}
    persist_book_structure(store, project_id, structure)
    store.record_page_confirmation(
        project_id=project_id,
        page_index=0,
        kind="non_text",
        actor="human",
        reason="cover artwork, no text to transcribe",
    )

    book = assemble_verified_content(store, project_id)
    data = _epub(book)

    assert roles[0] is PageRole.COVER
    assert ink[0] is not None and ink[0] >= 0.5
    assert book.cover_image is not None
    assert book.cover_image.data == rendered.image_bytes
    assert book.cover_image.media_type == "image/png"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        package = archive.read("OEBPS/content.opf").decode("utf-8")
        embedded = archive.read("OEBPS/images/page-000001.png")
        # A one-page book has no chapter tree, so the facsimile layout applies.
        front = archive.read("OEBPS/page-000001.xhtml").decode("utf-8")
    assert 'properties="cover-image"' in package
    assert embedded == rendered.image_bytes
    assert '<img src="images/page-000001.png" alt="封面"/>' in front


# ------------------------------------------------------------- reader acceptance

#: Structural acceptance of what a reading system (Readium / epub.js / a library
#: grid) needs: a navigable table of contents, manifest properties for cover and
#: nav, and every spine document reachable from the navigation.
def test_epub_is_reader_acceptable() -> None:
    cover = _image(0, _PNG, PageImageRole.COVER)
    plate = _image(2, _PNG, PageImageRole.FIGURE)
    book = _book_with((cover, plate), pages=4)

    data = _epub(book)

    # build_epub runs check_epub internally; invoke it explicitly as the gate.
    check_epub(data, book)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")
        package = archive.read("OEBPS/content.opf").decode("utf-8")
        front = archive.read("OEBPS/doc-000001.xhtml").decode("utf-8")
        chapter = archive.read("OEBPS/doc-000002.xhtml").decode("utf-8")

    assert 'epub:type="toc"' in nav
    assert 'properties="nav"' in package
    assert 'properties="cover-image"' in package
    assert '<meta name="cover" content="image-page-0"/>' in package
    assert '<img src="images/page-000001.png" alt="封面"/>' in front
    assert '<img src="images/page-000003.png" alt="第 3 页的插图"/>' in chapter
