"""Cross-page flow: a paragraph must not stop because the scan turned a page.

The page-shaped model is right for evidence and wrong for reading. These tests pin
the two halves of that claim: the merge rules (what may join, what must not), and
the guarantee that regrouping never loses, duplicates or reorders a page.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scribeforge.adapters.epub import build_epub
from scribeforge.adapters.export_check import ExportIntegrityError, check_epub
from scribeforge.domain.content import VerifiedBook, VerifiedChapter, VerifiedPage
from scribeforge.domain.flow import build_flow
from scribeforge.domain.ocr import BoundingBox
from scribeforge.domain.paragraphs import LineRole, ends_sentence

#: Left margin and measure of a typical 32-open page, in normalized units.
LEFT = 0.06
RIGHT = 0.88
PITCH = 0.028
MEASURE = 28
CHAR = (RIGHT - LEFT) / MEASURE


def _box(index: int, length: int, *, indent: int = 0) -> BoundingBox:
    return BoundingBox(LEFT + indent * CHAR, 0.06 + index * PITCH, CHAR * length, PITCH * 0.7)


def _page(
    page_index: int,
    *lines: tuple[str, int, int],
    printed_label: str = "",
    role: LineRole = LineRole.BODY,
) -> VerifiedPage:
    """A page from ``(text, row, indent)`` triples."""

    return VerifiedPage(
        page_index,
        tuple(text for text, _, _ in lines),
        printed_label=printed_label,
        line_boxes=tuple(_box(row, len(text), indent=indent) for text, row, indent in lines),
        line_roles=tuple(role for _ in lines),
    )


def _full(text: str, row: int) -> tuple[str, int, int]:
    """A line that fills the measure (so it looks like a line that ran out of room)."""

    return (text, row, 0)


def _short(text: str, row: int, *, indent: int = 0) -> tuple[str, int, int]:
    return (text, row, indent)


def _book(*pages: VerifiedPage, chapters: tuple[VerifiedChapter, ...] = ()) -> VerifiedBook:
    return VerifiedBook("V.", tuple(pages), chapters=chapters)


# ------------------------------------------------------------------ merging


def test_a_paragraph_continues_across_a_page_turn() -> None:
    """The core case: page 0 ends mid-sentence at full measure, page 1 continues."""

    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥一二三四", 0)),
        _page(1, _full("五六七八九十句号在这里。", 0)),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    flow = build_flow(book)

    assert len(flow.paragraphs) == 1
    paragraph = flow.paragraphs[0]
    assert paragraph.text == "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥一二三四五六七八九十句号在这里。"
    assert (paragraph.start_page, paragraph.end_page) == (0, 1)


def test_a_page_that_closes_its_sentence_does_not_continue() -> None:
    book = _book(
        _page(0, _full("这一页在句号处结束了。", 0)),
        _page(1, _full("下一页开始新的内容。", 0)),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    assert [paragraph.text for paragraph in build_flow(book).paragraphs] == [
        "这一页在句号处结束了。",
        "下一页开始新的内容。",
    ]


def test_a_page_whose_last_line_stops_short_ends_its_paragraph() -> None:
    """A short final line is the typesetter saying "this paragraph ended here"."""

    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未", 0), _short("短行", 1)),
        _page(1, _full("下一页的内容从这里开始而不是接着上文继续写", 0)),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    paragraphs = build_flow(book).paragraphs

    assert [paragraph.text for paragraph in paragraphs] == [
        "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未短行",
        "下一页的内容从这里开始而不是接着上文继续写",
    ]


def test_an_indented_first_line_starts_a_new_paragraph_on_the_next_page() -> None:
    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥", 0)),
        _page(
            1,
            _short("新的段落缩进了", 0, indent=2),
            _short("后面的正文行。", 1),
        ),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    assert len(build_flow(book).paragraphs) == 2


def test_a_blank_page_breaks_the_continuation() -> None:
    """Pages must be adjacent: a textless leaf ends whatever was running."""

    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥", 0)),
        _page(1),
        _page(2, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥", 0)),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    paragraphs = build_flow(book).paragraphs

    assert len(paragraphs) == 2
    assert [(item.start_page, item.end_page) for item in paragraphs] == [(0, 0), (2, 2)]


def test_a_heading_or_footnote_never_joins_across_pages() -> None:
    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥", 0)),
        VerifiedPage(
            1,
            ("① 注释文字",),
            line_boxes=(_box(0, 5),),
            line_roles=(LineRole.FOOTNOTE,),
        ),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    paragraphs = build_flow(book).paragraphs

    assert len(paragraphs) == 2
    assert paragraphs[1].role is LineRole.FOOTNOTE


def test_merging_only_happens_inside_a_document() -> None:
    """A chapter boundary is a reading boundary: the new chapter starts a page."""

    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥", 0)),
        _page(1, _full("五六七八九十句号在这里。", 0)),
        chapters=(
            VerifiedChapter("第一章", 1, 0),
            VerifiedChapter("第二章", 1, 1),
        ),
    )

    flow = build_flow(book)

    assert [document.title for document in flow.documents] == ["第一章", "第二章"]
    assert [len(document.paragraphs) for document in flow.documents] == [1, 1]


def test_pages_before_the_first_chapter_become_front_matter() -> None:
    book = _book(
        _page(0, _short("出版说明", 0), printed_label="1"),
        _page(1, _full("第一章的正文。", 0), printed_label="2"),
        chapters=(VerifiedChapter("第一章", 1, 1),),
    )

    flow = build_flow(book)

    assert [document.title for document in flow.documents] == ["Front matter", "第一章"]
    assert flow.documents[0].pages == (0,)
    assert flow.documents[1].pages == (1,)


# ------------------------------------------------------------------ layout


def test_a_book_without_chapters_keeps_one_document_per_page() -> None:
    book = _book(_page(0, _full("甲乙。", 0)), _page(1, _full("丙丁。", 0)))

    flow = build_flow(book)

    assert flow.layout == "page"
    assert [document.file_name for document in flow.documents] == [
        "page-000001.xhtml",
        "page-000002.xhtml",
    ]
    assert [document.item_id for document in flow.documents] == ["page-0", "page-1"]


def test_page_layout_can_be_forced_on_a_book_with_chapters() -> None:
    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥", 0)),
        _page(1, _full("五六七八九十句号在这里。", 0)),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    flow = build_flow(book, layout="page")

    assert flow.layout == "page"
    assert [len(document.paragraphs) for document in flow.documents] == [1, 1]


def test_a_chapter_tree_that_misses_the_book_falls_back_to_pages() -> None:
    """A stale chapter pointing at a page this book does not have is not structure.

    Building "doc" documents out of single pages would also mislabel them, so the
    flow falls back to the facsimile shape and says so in ``layout``.
    """

    book = _book(
        _page(0, _full("甲乙。", 0)),
        _page(1, _full("丙丁。", 0)),
        chapters=(VerifiedChapter("第十二章", 1, 40),),
    )

    flow = build_flow(book)

    assert flow.layout == "page"
    assert [document.file_name for document in flow.documents] == [
        "page-000001.xhtml",
        "page-000002.xhtml",
    ]


def test_flow_rejects_an_unknown_layout() -> None:
    with pytest.raises(ValueError, match="unknown layout"):
        build_flow(_book(_page(0, _short("甲乙。", 0))), layout="scroll")


def test_flow_keeps_every_page_and_every_character() -> None:
    """Regrouping is a re-flow, never an edit.

    A merged paragraph spans a page turn, so text cannot be attributed to a single
    page afterwards. What must hold is the invariant the export check enforces:
    per document, the paragraphs are exactly the accepted lines of its pages, in
    order, with nothing added or lost.
    """

    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥一二三四", 0)),
        _page(1, _full("五六七八九十句号在这里。", 0), _short("新的段落。", 1, indent=2)),
        _page(2, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥", 0)),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    flow = build_flow(book)
    spans = {page.page_index: page for page in book.pages}
    for document in flow.documents:
        rendered = "".join(paragraph.text for paragraph in document.paragraphs)
        accepted = "".join(
            line for page_index in document.pages for line in spans[page_index].lines
        )
        assert "".join(rendered.split()) == "".join(accepted.split())
    # The paragraph that ran off page 0 is one unit spanning two pages; the short
    # indented line on page 1 starts another; page 2 starts a third because the
    # previous page ended a paragraph rather than continuing one.
    assert [(item.start_page, item.end_page) for item in flow.paragraphs] == [
        (0, 1),
        (1, 1),
        (2, 2),
    ]


# -------------------------------------------------------------------- epub


def _epub(book: VerifiedBook, *, layout: str = "auto") -> bytes:
    return build_epub(
        book,
        identifier="urn:scribeforge:test",
        language="zh",
        modified=datetime(2026, 10, 29, tzinfo=UTC),
        layout=layout,
    )


def test_epub_writes_one_document_per_chapter_with_page_markers() -> None:
    book = _book(
        _page(0, _full("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥一二三四", 0), printed_label="1"),
        _page(1, _full("五六七八九十句号在这里。", 0), printed_label="2"),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    with zipfile.ZipFile(io.BytesIO(_epub(book))) as archive:
        names = set(archive.namelist())
        document = archive.read("OEBPS/doc-000001.xhtml").decode("utf-8")

    assert "OEBPS/doc-000001.xhtml" in names
    assert not any(name.startswith("OEBPS/page-") for name in names)
    # Both scanned pages leave a marker, in order, carrying the printed folio.
    assert document.index('id="page-0" title="1"') < document.index('id="page-1" title="2"')
    # And the paragraph is not cut at the page turn.
    assert "<p>甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥一二三四五六七八九十句号在这里。</p>" in document


def test_epub_page_layout_still_writes_one_file_per_scan() -> None:
    book = _book(
        _page(0, _full("甲乙。", 0)),
        _page(1, _full("丙丁。", 0)),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )

    with zipfile.ZipFile(io.BytesIO(_epub(book, layout="page"))) as archive:
        names = set(archive.namelist())

    assert {"OEBPS/page-000001.xhtml", "OEBPS/page-000002.xhtml"} <= names


def test_a_document_that_loses_a_page_break_is_rejected() -> None:
    book = _book(
        _page(0, _full("甲乙。", 0), printed_label="1"),
        _page(1, _full("丙丁。", 0), printed_label="2"),
        chapters=(VerifiedChapter("第一章", 1, 0),),
    )
    data = _epub(book)

    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(buffer, "w") as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            if info.filename == "OEBPS/doc-000001.xhtml":
                payload = payload.replace(
                    b'<span epub:type="pagebreak" role="doc-pagebreak" '
                    b'id="page-1" title="2"/>',
                    b"",
                )
            target.writestr(info, payload)

    with pytest.raises(ExportIntegrityError, match="marker|text differs"):
        check_epub(buffer.getvalue(), book)


# ---------------------------------------------------------------- benchmark

CORPUS = Path(__file__).resolve().parent.parent / "benchmark" / "corpus" / "v"


def _typeset(paragraph: str) -> list[str]:
    """Break a paragraph into visual lines at this book's measure.

    The first line is two characters shorter because it is indented, and a Latin
    word is never split, so the last piece of a paragraph is the only short one.
    """

    pieces: list[str] = []
    cursor = 0
    length = MEASURE - 2
    while cursor < len(paragraph):
        end = min(cursor + length, len(paragraph))
        while (
            end < len(paragraph)
            and end > cursor + 1
            and paragraph[end - 1].isascii()
            and paragraph[end].isascii()
            and paragraph[end - 1].isalnum()
            and paragraph[end].isalnum()
        ):
            end -= 1
        pieces.append(paragraph[cursor:end])
        cursor = end
        length = MEASURE
    return pieces


def _lines_for(paragraphs: list[str], *, indent_first: bool) -> list[tuple[str, int, int]]:
    rows: list[tuple[str, int, int]] = []
    for position, paragraph in enumerate(paragraphs):
        for piece_index, piece in enumerate(_typeset(paragraph)):
            indent = 2 if piece_index == 0 and (position or indent_first) else 0
            rows.append((piece, len(rows), indent))
    return rows


@pytest.mark.benchmark
def test_a_real_page_turn_inside_a_paragraph_is_undone() -> None:
    """The merge rule, measured on the hand transcription of the real book.

    Page images cannot be read here (no GPU, and this PDF has no text layer), so
    the lines are re-typeset from the real transcription at the book's real
    measure, and the page break is placed inside the page's longest paragraph —
    where a scan's page break lands whenever it does not fall at a paragraph end.
    The text, the measure and the indent convention are real; only the position of
    the cut is chosen by the test. That is exactly the input the rule exists to
    undo.
    """

    manifest = CORPUS / "manifest.json"
    if not manifest.is_file():
        pytest.skip("benchmark corpus is not available")
    data = json.loads(manifest.read_text(encoding="utf-8"))

    merged = 0
    characters = 0
    for case in data["cases"]:
        truth = [
            line.strip()
            for line in (CORPUS / case["ground_truth"]).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        pieces_by_paragraph = [_typeset(paragraph) for paragraph in truth]
        # The longest paragraph is the one a real page turn is most likely to cut,
        # and it is the only one with a full measure line in the middle to cut at.
        broken = max(range(len(truth)), key=lambda index: len(pieces_by_paragraph[index]))
        pieces = pieces_by_paragraph[broken]
        # At least two lines of the paragraph stay on the first page, so that page
        # still has a line at the body's left margin: without one the page cannot
        # tell an indented line from an unindented one, which is a different (and
        # already documented) limitation, not what this test is about.
        cut = next(
            (
                candidate
                for candidate in range(2, len(pieces))
                if not ends_sentence(pieces[candidate - 1])
            ),
            None,
        )
        if cut is None:
            continue

        page_zero = _lines_for(truth[:broken], indent_first=True)
        page_zero.append((pieces[0], len(page_zero), 2))
        for piece in pieces[1:cut]:
            page_zero.append((piece, len(page_zero), 0))
        if len(page_zero) < 2:
            continue
        page_one = [(pieces[cut], 0, 0)]
        for piece in pieces[cut + 1 :]:
            page_one.append((piece, len(page_one), 0))
        for paragraph in truth[broken + 1 :]:
            for piece, _, indent in _lines_for([paragraph], indent_first=True):
                page_one.append((piece, len(page_one), indent))
        if not page_one:
            continue

        book = _book(
            _page(0, *page_zero),
            _page(1, *page_one),
            chapters=(VerifiedChapter("第一章", 1, 0),),
        )
        flow = build_flow(book)

        assert len(flow.paragraphs) == len(truth), (
            f"page {case['id']}: {len(flow.paragraphs)} paragraphs, expected {len(truth)}"
        )
        carried = flow.paragraphs[broken]
        assert (carried.start_page, carried.end_page) == (0, 1), (
            f"page {case['id']}: the page turn split paragraph {broken}"
        )
        # Joining the two halves must reproduce the paragraph the author wrote;
        # a Latin word break legitimately gains a space, so compare squashed text.
        assert "".join(carried.text.split()) == "".join(truth[broken].split())
        merged += 1
        characters += sum(len("".join(paragraph.split())) for paragraph in truth)

    assert merged == 6
    assert characters > 0
