"""Page roles, contents-page parsing and printed→physical page mapping (#87).

The question these tests answer: given only what engines reported and what the
raster measures, can the pipeline say what a page *is* — cover, copyright,
contents, front matter, body, plate, blank — and turn the book's own contents page
into a chapter tree anchored to physical pages?
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scribeforge.adapters.page_ink import (
    DEFAULT_INK_THRESHOLD,
    measure_page_ink,
)
from scribeforge.domain.ocr import BlockType, BoundingBox, OCRBlock, OCRLine, PageOCRResult
from scribeforge.domain.structure import (
    PLATE_INK_RATIO,
    BookStructure,
    Chapter,
    NumberingScheme,
    PageLabel,
    PageRole,
    PageStructure,
    RoleEvidence,
    TocEntry,
    TocPage,
    classify_page_roles,
    detect_page_roles,
    looks_like_toc_page,
    map_toc_chapters,
    parse_toc_entry,
    toc_entry_kind,
)
from scribeforge.pipeline.structure import build_book_structure, persist_book_structure
from scribeforge.storage.sqlite import SQLiteStore

# --------------------------------------------------------------------- ink


def test_a_white_page_measures_zero_ink(tmp_path: Path) -> None:
    path = tmp_path / "white.png"
    Image.new("L", (200, 200), 255).save(path)

    measurement = measure_page_ink(path)

    assert measurement.ratio == 0.0
    assert (measurement.width, measurement.height) == (200, 200)
    assert measurement.threshold == DEFAULT_INK_THRESHOLD


def test_a_half_inked_page_measures_half(tmp_path: Path) -> None:
    path = tmp_path / "half.png"
    image = Image.new("L", (200, 200), 255)
    for x in range(100):
        for y in range(200):
            image.putpixel((x, y), 0)
    image.save(path)

    assert measure_page_ink(path).ratio == pytest.approx(0.5)


def test_a_large_page_is_reduced_before_measuring(tmp_path: Path) -> None:
    path = tmp_path / "big.png"
    Image.new("L", (2000, 3000), 255).save(path)

    measurement = measure_page_ink(path, max_dimension=256)

    assert max(measurement.width, measurement.height) <= 256
    assert measurement.ratio == 0.0


def test_ink_measurement_rejects_impossible_parameters(tmp_path: Path) -> None:
    path = tmp_path / "white.png"
    Image.new("L", (10, 10), 255).save(path)

    with pytest.raises(ValueError):
        measure_page_ink(path, max_dimension=0)
    with pytest.raises(ValueError):
        measure_page_ink(path, threshold=999)


def test_a_missing_raster_is_an_error_not_a_blank_page(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        measure_page_ink(tmp_path / "nope.png")


# ------------------------------------------------------------------- roles


def _evidence(
    page_index: int,
    lines: tuple[str, ...] = (),
    *,
    label: PageLabel | None = None,
    ink: float | None = None,
    headings: tuple[str, ...] = (),
) -> RoleEvidence:
    return RoleEvidence(
        page_index=page_index,
        lines=lines,
        label=label or PageLabel("", NumberingScheme.NONE, None),
        ink_ratio=ink,
        headings=headings,
    )


def _numbered(page_index: int, number: int, run: int = 1) -> PageLabel:
    return PageLabel(
        str(number), NumberingScheme.ARABIC, number, run * 1_000_000 + number
    )


def test_a_page_without_text_or_ink_is_blank() -> None:
    assert detect_page_roles(3, text="  ", ink_ratio=0.0).value == "blank"


def test_a_page_without_text_but_with_ink_is_a_plate() -> None:
    assert detect_page_roles(3, text="", ink_ratio=0.4).value == "plate"
    assert detect_page_roles(3, text="", ink_ratio=PLATE_INK_RATIO).value == "plate"
    assert detect_page_roles(3, text="", ink_ratio=PLATE_INK_RATIO / 2).value == "blank"


def test_text_alone_never_decides_a_contextual_role() -> None:
    """Cover/copyright/TOC need the whole book; one page cannot decide them."""

    assert detect_page_roles(0, text="书名") is PageRole.UNKNOWN


def test_blank_and_plate_are_classified_together_from_ink() -> None:
    roles = classify_page_roles(
        [
            _evidence(0, ("书名",), ink=0.05),
            _evidence(1, (), ink=0.0),
            _evidence(2, (), ink=0.5),
        ]
    )

    assert roles[1] is PageRole.BLANK
    assert roles[2] is PageRole.PLATE


def test_a_short_first_page_is_the_cover() -> None:
    roles = classify_page_roles(
        [
            _evidence(0, ("V.",), ink=0.08),
            _evidence(1, ("正文第一行",), label=_numbered(1, 1)),
            _evidence(2, ("正文第二行",), label=_numbered(2, 2)),
        ]
    )

    assert roles[0] is PageRole.COVER


def test_a_first_page_with_body_density_is_not_a_cover() -> None:
    long_text = "这是一段很长的正文，几乎占满整页，绝不可能是一本书的封面文字。" * 2
    roles = classify_page_roles(
        [
            _evidence(0, (long_text,), ink=0.3),
            _evidence(1, ("正文",), label=_numbered(1, 1)),
        ]
    )

    assert roles[0] is not PageRole.COVER


def test_a_first_page_that_starts_a_chapter_is_not_a_cover() -> None:
    roles = classify_page_roles(
        [
            _evidence(0, ("第一章",), ink=0.2, headings=("第一章",)),
            _evidence(1, ("正文",), label=_numbered(1, 1)),
        ]
    )

    assert roles[0] is not PageRole.COVER


def test_a_copyright_page_is_recognised_by_its_own_data() -> None:
    roles = classify_page_roles(
        [
            _evidence(0, ("书名",), ink=0.05),
            _evidence(
                1,
                (
                    "图书在版编目（CIP）数据",
                    "V. / 托马斯·品钦著．— 南京：译林出版社，2008.1",
                    "ISBN 978-7-5447-0475-2",
                    "责任编辑 张三",
                ),
                ink=0.2,
            ),
            _evidence(2, ("正文",), label=_numbered(1, 1)),
        ]
    )

    assert roles[1] is PageRole.COPYRIGHT


def test_a_body_page_mentioning_a_publisher_is_not_a_copyright_page() -> None:
    roles = classify_page_roles(
        [
            _evidence(1, ("译林出版社出版了这本书。",), label=_numbered(1, 1), ink=0.2),
            _evidence(2, ("下一段。",), label=_numbered(2, 2), ink=0.2),
        ]
    )

    assert roles[1] is not PageRole.COPYRIGHT


def test_front_matter_body_and_back_matter_follow_the_longest_numbering_run() -> None:
    pages = [
        _evidence(0, ("书名",), ink=0.05),
        # A short arabic run before the body: front matter that restarted at 1.
        _evidence(1, ("出版说明",), label=_numbered(1, 1), ink=0.2),
        _evidence(2, ("出版说明续",), label=_numbered(2, 2), ink=0.2),
        # The body: the longest run, numbering restarted by the chapter.
        _evidence(3, ("第一章",), label=_numbered(3, 1, run=2), ink=0.3),
        _evidence(4, ("正文",), label=_numbered(4, 2, run=2), ink=0.3),
        _evidence(5, ("正文",), label=_numbered(5, 3, run=2), ink=0.3),
        # Back matter, unnumbered.
        _evidence(6, ("译后记",), ink=0.2),
    ]
    roles = classify_page_roles(pages)

    assert roles[0] is PageRole.COVER
    assert roles[1] is PageRole.FRONT_MATTER
    assert roles[2] is PageRole.FRONT_MATTER
    assert [roles[3], roles[4], roles[5]] == [PageRole.BODY] * 3
    assert roles[6] is PageRole.BACK_MATTER


def test_a_book_without_printed_numbers_leaves_pages_unclassified() -> None:
    """No numbering means no way to tell front matter from body: say so."""

    roles = classify_page_roles(
        [
            _evidence(0, ("封面书名",), ink=0.05),
            _evidence(1, ("一段文字",), ink=0.2),
        ]
    )

    assert roles[1] is PageRole.UNKNOWN


def test_a_contents_page_is_recognised_by_its_heading() -> None:
    roles = classify_page_roles(
        [
            _evidence(
                0,
                ("目录", "第一章 开端……1", "第二章 发展……45", "第三章 结局……90"),
                ink=0.2,
            ),
            _evidence(1, ("正文",), label=_numbered(1, 1)),
        ]
    )

    assert roles[0] is PageRole.TABLE_OF_CONTENTS


# --------------------------------------------------------------------- TOC


def test_a_contents_line_yields_title_and_printed_page() -> None:
    entry = parse_toc_entry("第一章 万有引力之虹 ………… 13")

    assert entry is not None
    assert entry.title == "第一章 万有引力之虹"
    assert entry.printed_label == "13"
    assert entry.kind == "chapter"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("出版说明  1", "1"),
        ("译者序 9", "9"),
        ("附录 甲 i", "i"),
        ("后记 …… 附录", None),
    ],
)
def test_contents_lines_parse_or_are_rejected(line: str, expected: str | None) -> None:
    entry = parse_toc_entry(line)

    if expected is None:
        assert entry is None
    else:
        assert entry is not None
        assert entry.printed_label == expected


@pytest.mark.parametrize(
    "line",
    [
        "",
        "目录",
        "第一章",
        "他出生于1923",  # prose that ends in a number
        "这是一行没有任何页码的正文内容",
        "……12",
    ],
)
def test_lines_that_are_not_contents_entries_are_rejected(line: str) -> None:
    assert parse_toc_entry(line) is None


def test_toc_entry_kinds_follow_the_numbering() -> None:
    assert toc_entry_kind("第一部 家族") == "part"
    assert toc_entry_kind("第一章 开端") == "chapter"
    assert toc_entry_kind("第三节 转折") == "section"
    assert toc_entry_kind("译者序") == "entry"


def test_a_contents_page_is_not_a_body_page() -> None:
    assert looks_like_toc_page(["目录", "第一章 …… 1", "第二章 …… 2"])
    assert looks_like_toc_page(["第一章 …… 1", "第二章 …… 2", "第三章 …… 3"])
    assert not looks_like_toc_page(["正文第一行。", "正文第二行。", "正文第三行。"])
    assert not looks_like_toc_page([])


def _toc_page(page_index: int, number: int, headings: tuple[str, ...]) -> TocPage:
    return TocPage(
        page_index=page_index,
        label=PageLabel(str(number), NumberingScheme.ARABIC, number, number),
        headings=headings,
    )


def test_toc_entries_anchor_only_where_the_page_heading_confirms_them() -> None:
    """Three sections renumber from 1, so a label alone is ambiguous.

    The mapping must use the page's own heading as the second signal, and leave
    every ambiguous entry unmapped rather than placing it wrongly.
    """

    mapping = map_toc_chapters(
        (
            TocEntry("出版说明", "1", "entry"),
            TocEntry("第一章 开端", "1", "chapter"),
            TocEntry("第二章 发展", "2", "chapter"),
        ),
        (
            _toc_page(4, 1, ("出版说明",)),  # front matter, numbered 1
            _toc_page(18, 1, ("第一章 开端",)),  # body, numbered 1 again
            _toc_page(19, 2, ()),  # body page 2 reports no heading at all
        ),
    )

    assert [(chapter.title, chapter.start_page_index) for chapter in mapping.chapters] == [
        ("出版说明", 4),
        ("第一章 开端", 18),
    ]
    assert [entry.title for entry in mapping.unmapped] == ["第二章 发展"]


def test_a_unique_numbered_page_with_a_heading_is_enough() -> None:
    mapping = map_toc_chapters(
        (TocEntry("第三章 收束", "77", "chapter"),),
        (_toc_page(90, 77, ("第三章 收束",)),),
    )

    assert mapping.chapters[0].start_page_index == 90
    assert mapping.chapters[0].source == "toc"


def test_chapters_are_ordered_by_page_even_when_the_toc_is_not() -> None:
    mapping = map_toc_chapters(
        (
            TocEntry("附录", "90", "entry"),
            TocEntry("第一章 开端", "1", "chapter"),
            TocEntry("第二章 发展", "2", "chapter"),
        ),
        (
            _toc_page(10, 1, ("第一章 开端",)),
            _toc_page(20, 2, ("第二章 发展",)),
            _toc_page(30, 90, ("附录",)),
        ),
    )

    assert [chapter.start_page_index for chapter in mapping.chapters] == [10, 20, 30]
    assert [chapter.ordinal for chapter in mapping.chapters] == [0, 1, 2]


def test_a_part_numbering_pushes_chapters_one_level_down() -> None:
    mapping = map_toc_chapters(
        (
            TocEntry("第一部 家族", "1", "part"),
            TocEntry("第一章 开端", "3", "chapter"),
        ),
        (
            _toc_page(10, 1, ("第一部 家族",)),
            _toc_page(12, 3, ("第一章 开端",)),
        ),
    )

    assert [chapter.level for chapter in mapping.chapters] == [1, 2]


# ------------------------------------------------------------- whole book


def _line(text: str, *, y: float, height: float = 0.02) -> OCRLine:
    return OCRLine(text, BoundingBox(0.1, y, 0.8, height), ())


def _page(
    lines: list[OCRLine],
    *,
    page_index: int,
    blocks: tuple[OCRBlock, ...] = (),
) -> PageOCRResult:
    return PageOCRResult(
        page_index=page_index,
        engine="fixture",
        engine_version="1.0",
        lines=tuple(lines),
        blocks=blocks,
    )


def _footer_block(line_start: int, y: float) -> OCRBlock:
    return OCRBlock(
        block_type=BlockType.FOOTER,
        box=BoundingBox(0.4, y, 0.2, 0.015),
        line_start=line_start,
        line_end=line_start + 1,
    )


def _numbered_page(text: str, number: str, page_index: int) -> PageOCRResult:
    y = 0.96
    return _page(
        [_line(text, y=0.2), _line(number, y=y, height=0.015)],
        page_index=page_index,
        blocks=(_footer_block(1, y),),
    )


def _titled_page(title: str, number: str, page_index: int) -> PageOCRResult:
    """A chapter opening page: a declared title block above body lines."""

    title_line = _line(title, y=0.2, height=0.05)
    body = _line("正文第一行。", y=0.3)
    footer_y = 0.96
    return _page(
        [title_line, body, _line(number, y=footer_y, height=0.015)],
        page_index=page_index,
        blocks=(
            OCRBlock(
                block_type=BlockType.TITLE,
                box=BoundingBox(0.3, 0.2, 0.4, 0.05),
                line_start=0,
                line_end=1,
            ),
            _footer_block(2, footer_y),
        ),
    )


def _store_with(tmp_path: Path, pages: list[PageOCRResult]) -> tuple[SQLiteStore, int]:
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", "v.pdf", "hash")
    for result in pages:
        store.add_page(
            project_id, result.page_index, f"page-{result.page_index:03d}.png", 100, 150
        )
        store.record_ocr(project_id, result)
    return store, project_id


def test_the_contents_page_supplies_the_chapter_tree(tmp_path: Path) -> None:
    """The whole point of #87: a real TOC, not one entry per physical page."""

    pages = [
        _page(
            [
                _line("目录", y=0.1),
                _line("第一章 开端 …… 1", y=0.2),
                _line("第二章 发展 …… 3", y=0.3),
            ],
            page_index=0,
        ),
        _titled_page("第一章 开端", "1", 1),
        _numbered_page("正文。", "2", 2),
        _titled_page("第二章 发展", "3", 3),
    ]
    store, project_id = _store_with(tmp_path, pages)

    structure = build_book_structure(
        store, project_id, engine="fixture", measure_ink_ratio=False
    )

    assert [chapter.title for chapter in structure.chapters] == ["第一章 开端", "第二章 发展"]
    assert [chapter.start_page_index for chapter in structure.chapters] == [1, 3]
    assert all(chapter.source == "toc" for chapter in structure.chapters)
    assert structure.chapters[0].printed_page == "1"
    assert structure.toc_entries == 2


def test_headings_are_the_fallback_when_there_is_no_usable_toc(tmp_path: Path) -> None:
    pages = [
        _titled_page("第一章 开端", "1", 0),
        _numbered_page("正文。", "2", 1),
    ]
    store, project_id = _store_with(tmp_path, pages)

    structure = build_book_structure(
        store, project_id, engine="fixture", measure_ink_ratio=False
    )

    assert [chapter.title for chapter in structure.chapters] == ["第一章 开端"]
    assert structure.chapters[0].source == "heading"


def test_structure_reports_roles_and_chapter_source(tmp_path: Path) -> None:
    image = tmp_path / "blank.png"
    Image.new("L", (60, 90), 255).save(image)
    pages = [_numbered_page("正文。", "1", 0)]
    store, project_id = _store_with(tmp_path, pages)
    store.add_page(project_id, 1, str(image), 60, 90)
    store.record_ocr(project_id, _page([], page_index=1))

    structure = build_book_structure(store, project_id, engine="fixture")
    report = persist_book_structure(store, project_id, structure)

    assert report.blank_pages == (1,)
    assert report.confirmed_textless == (1,)
    assert report.unmeasured_pages == ()
    assert report.roles["blank"] == 1
    assert report.chapter_source == "none"


def test_an_unreadable_raster_is_reported_as_unmeasured(tmp_path: Path) -> None:
    """A moved asset must not be mistaken for a blank page."""

    store, project_id = _store_with(tmp_path, [_page([], page_index=0)])

    structure = build_book_structure(store, project_id, engine="fixture")
    report = persist_book_structure(store, project_id, structure)

    assert structure.pages[0].role is PageRole.BLANK
    assert structure.pages[0].ink_ratio is None
    assert report.unmeasured_pages == (0,)
    assert report.confirmed_textless == ()
    assert store.list_page_confirmations(project_id, 0) == ()


def test_a_plate_page_is_reported_but_never_auto_confirmed(tmp_path: Path) -> None:
    image = tmp_path / "plate.png"
    picture = Image.new("L", (60, 90), 255)
    for x in range(20):
        for y in range(30):
            picture.putpixel((x, y), 0)
    picture.save(image)
    store, project_id = _store_with(tmp_path, [_page([], page_index=0)])
    store.add_page(project_id, 1, str(image), 60, 90)
    store.record_ocr(project_id, _page([], page_index=1))

    structure = build_book_structure(store, project_id, engine="fixture")
    report = persist_book_structure(store, project_id, structure)

    assert report.plate_pages == (1,)
    assert 1 not in report.confirmed_textless
    assert store.list_page_confirmations(project_id, 1) == ()


def test_page_ink_round_trips_through_the_store(tmp_path: Path) -> None:
    image = tmp_path / "blank.png"
    Image.new("L", (60, 90), 255).save(image)
    store, project_id = _store_with(tmp_path, [_page([], page_index=0)])
    store.record_page_structure(
        project_id,
        (
            PageStructure(0, PageRole.BLANK, ink_ratio=0.0125),
        ),
    )

    loaded = store.load_book_structure(project_id)

    assert loaded.pages[0].ink_ratio == pytest.approx(0.0125)


def test_chapter_source_round_trips_through_the_store(tmp_path: Path) -> None:
    store, project_id = _store_with(tmp_path, [_page([_line("正文", y=0.2)], page_index=0)])
    store.record_chapters(
        project_id,
        (Chapter(0, 1, "第一章", 0, "1", source="toc"),),
    )

    loaded = store.load_book_structure(project_id)

    assert loaded.chapters[0].source == "toc"


def test_book_structure_carries_unplaced_contents_entries() -> None:
    structure = BookStructure(
        pages=(PageStructure(0, PageRole.BODY),),
        chapters=(),
        toc_entries=3,
        unplaced_toc_titles=("第二章 发展",),
    )

    assert structure.toc_entries == 3
    assert structure.unplaced_toc_titles == ("第二章 发展",)


def _image_with_ink_fraction(path: Path, fraction: float, size: int = 100) -> Path:
    """A white page with a dark block covering ``fraction`` of its pixels."""

    image = Image.new("L", (size, size), 255)
    dark = round(size * size * fraction)
    for index in range(dark):
        image.putpixel((index % size, index // size), 0)
    image.save(path)
    return path


def test_a_nearly_inked_textless_page_stays_a_human_decision(tmp_path: Path) -> None:
    """Between the two thresholds the measurement runs out of authority.

    1.5% ink is far more than a blank leaf (0.4% measured on this book) but far
    less than a body page: it could be a stamp, a signature, or one line of type
    nobody read. Confirm automatically and the pipeline would silently drop it.
    """

    image = _image_with_ink_fraction(tmp_path / "sparse.png", 0.015)
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", "v.pdf", "hash")
    store.add_page(project_id, 0, str(image), 100, 100)
    store.record_ocr(
        project_id,
        PageOCRResult(0, "fixture", "1.0", ()),
    )

    structure = build_book_structure(store, project_id, engine="fixture")
    report = persist_book_structure(store, project_id, structure)

    assert structure.pages[0].role is PageRole.BLANK
    assert structure.pages[0].ink_ratio == pytest.approx(0.015)
    assert report.confirmed_textless == ()
    assert store.list_page_confirmations(project_id, 0) == ()


def test_a_page_below_the_blank_threshold_is_confirmed_by_measurement(
    tmp_path: Path,
) -> None:
    image = _image_with_ink_fraction(tmp_path / "noise.png", 0.004)
    store = SQLiteStore(tmp_path / "project.sqlite3")
    store.initialize()
    project_id = store.create_project("V.", "v.pdf", "hash")
    store.add_page(project_id, 0, str(image), 100, 100)
    store.record_ocr(project_id, PageOCRResult(0, "fixture", "1.0", ()))

    structure = build_book_structure(store, project_id, engine="fixture")
    report = persist_book_structure(store, project_id, structure)

    assert report.confirmed_textless == (0,)
    confirmation = store.list_page_confirmations(project_id, 0)[0]
    assert (confirmation.kind, confirmation.actor) == ("blank", "evidence")
    assert "0.40%" in confirmation.reason


def test_a_dark_first_page_is_a_cover_even_with_a_long_caption() -> None:
    """This book's front cover measures 94% ink: it is a coloured page, not paper."""

    roles = classify_page_roles(
        [
            _evidence(
                0,
                ("V.", "凤凰文库·外国现当代文学系列", "〔美国〕托马斯·品钦 著", "译林出版社"),
                ink=0.94,
            ),
            _evidence(1, ("出版说明",), label=_numbered(1, 1), ink=0.04),
            _evidence(2, ("正文",), label=_numbered(2, 2), ink=0.11),
        ]
    )

    assert roles[0] is PageRole.COVER


def test_a_dark_page_that_is_not_first_is_not_a_cover() -> None:
    roles = classify_page_roles(
        [
            _evidence(0, ("出版说明",), label=_numbered(1, 1), ink=0.04),
            _evidence(1, ("正文",), label=_numbered(2, 2), ink=0.11),
            _evidence(2, ("图版说明",), label=_numbered(3, 3), ink=0.9),
        ]
    )

    assert roles[2] is not PageRole.COVER


def _accept_every_pair(store: SQLiteStore, project_id: int, pages: int) -> None:
    """Persist verification plus a human decision for every line of every page.

    Export reads only the decision layer, so an end-to-end test has to build it the
    way the pipeline does: two engine runs, an alignment, and one decision per pair.
    """

    from scribeforge.domain.alignment import LineAlignment, PageAlignment
    from scribeforge.domain.ocr import PageOCRResult
    from scribeforge.domain.risk import PairRisk

    for page_index in range(pages):
        primary_id = store.latest_engine_run(project_id, page_index, "fixture")
        primary = store.load_ocr_run(primary_id)
        secondary_id = store.record_ocr(
            project_id,
            PageOCRResult(
                page_index, "fixture-secondary", "1.0", primary.lines, primary.blocks
            ),
        )
        alignment = PageAlignment(
            page_index,
            "fixture",
            "fixture-secondary",
            tuple(
                LineAlignment(index, index, 1.0, 0.0, ())
                for index in range(len(primary.lines))
            ),
        )
        risks = tuple(PairRisk(index, 0.0, ()) for index in range(len(primary.lines)))
        store.record_verification(primary_id, secondary_id, alignment, risks, ())
        for index, line in enumerate(primary.lines):
            store.append_decision(
                project_id=project_id,
                page_index=page_index,
                pair_index=index,
                selected_text=line.text,
                actor="human",
                evidence_refs=(),
            )


def test_the_exported_epub_navigates_by_chapter_not_by_physical_page(
    tmp_path: Path,
) -> None:
    """The acceptance criterion of #87, end to end: a real table of contents.

    A 562-page book with 562 flat "Page N" entries has no table of contents. Here a
    contents page supplies the chapter list, the pages confirm it, and the EPUB nav
    must mirror the chapters instead of every raster.
    """

    import zipfile
    from datetime import UTC, datetime

    from scribeforge.adapters.epub import write_epub
    from scribeforge.pipeline.assembly import assemble_verified_content

    pages = [
        _page(
            [
                _line("目录", y=0.1),
                _line("第一章 开端 …… 1", y=0.2),
                _line("第二章 发展 …… 3", y=0.3),
            ],
            page_index=0,
        ),
        _titled_page("第一章 开端", "1", 1),
        _numbered_page("正文。", "2", 2),
        _titled_page("第二章 发展", "3", 3),
    ]
    store, project_id = _store_with(tmp_path, pages)
    _accept_every_pair(store, project_id, len(pages))
    persist_book_structure(
        store, project_id, build_book_structure(store, project_id, engine="fixture")
    )

    book = assemble_verified_content(store, project_id)
    out = tmp_path / "book.epub"
    write_epub(
        book,
        out,
        identifier="urn:scribeforge:test",
        language="zh",
        modified=datetime.now(UTC),
    )

    with zipfile.ZipFile(out) as archive:
        nav = archive.read("OEBPS/nav.xhtml").decode("utf-8")

    assert [chapter.title for chapter in book.chapters] == ["第一章 开端", "第二章 发展"]
    assert "第一章 开端" in nav
    assert "第二章 发展" in nav
    assert "Page 1" not in nav
