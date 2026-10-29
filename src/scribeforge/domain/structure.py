"""Book structure: page roles, printed page numbers and the chapter tree.

A scanned book has two independent notions of "page":

- the **physical** index (``page_index``), which is what the PDF and every stored
  evidence row uses;
- the **printed** label the reader sees in the margin, which restarts whenever the
  book starts a new numbered section.

Conflating them is the reason a 562-page export ends up with 562 flat navigation
entries called "Page 4", "Page 5", ... while the printed book starts a fresh
sequence at the first chapter and leaves some pages unnumbered. This module keeps
both, plus the roles that decide what a page even *is* (cover, body, plate, blank)
and the chapter tree that gives the EPUB a usable table of contents.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType


class PageRole(str, Enum):
    """What a page is, independent of what text it happens to contain."""

    COVER = "cover"
    FRONT_MATTER = "front_matter"
    COPYRIGHT = "copyright"
    TABLE_OF_CONTENTS = "table_of_contents"
    BODY = "body"
    PLATE = "plate"
    BLANK = "blank"
    BACK_MATTER = "back_matter"
    UNKNOWN = "unknown"

    @property
    def is_textual(self) -> bool:
        """True when the page carries running text a reader reads in order."""

        return self in {
            PageRole.FRONT_MATTER,
            PageRole.COPYRIGHT,
            PageRole.TABLE_OF_CONTENTS,
            PageRole.BODY,
            PageRole.BACK_MATTER,
        }


class NumberingScheme(str, Enum):
    """How a printed page label is written."""

    NONE = "none"
    ARABIC = "arabic"
    ROMAN_LOWER = "roman_lower"
    ROMAN_UPPER = "roman_upper"
    CJK = "cjk"
    UNKNOWN = "unknown"


_ROMAN_VALUES = {
    "i": 1,
    "v": 5,
    "x": 10,
    "l": 50,
    "c": 100,
    "d": 500,
    "m": 1000,
}

_CJK_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_CJK_UNITS = {"十": 10, "百": 100, "千": 1000}

# Characters a scanner or OCR pass may wrap around a folio.
_LABEL_NOISE = " 　.．·・-—–~～*＊()（）[]【】〈〉<>《》"


def parse_roman(text: str) -> int | None:
    """Parse a roman numeral, or return ``None`` if it is not one.

    Deliberately strict: "mix" is a word, not 1009. Only canonical, descending
    numeral forms are accepted.
    """

    lowered = text.strip().lower()
    if not lowered or any(character not in _ROMAN_VALUES for character in lowered):
        return None
    total = 0
    previous = 0
    for character in reversed(lowered):
        value = _ROMAN_VALUES[character]
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    # Reject non-canonical spellings ("iiii", "vv") by round-tripping.
    if format_roman(total).lower() != lowered:
        return None
    return total


def format_roman(value: int) -> str:
    numerals = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    remaining = value
    parts: list[str] = []
    for amount, numeral in numerals:
        while remaining >= amount:
            parts.append(numeral)
            remaining -= amount
    return "".join(parts)


def parse_cjk_numeral(text: str) -> int | None:
    """Parse a Chinese numeral such as 十二 or 二十三."""

    cleaned = text.strip()
    if not cleaned or any(
        character not in _CJK_DIGITS and character not in _CJK_UNITS
        for character in cleaned
    ):
        return None
    if len(cleaned) > 6:
        return None
    total = 0
    current = 0
    for character in cleaned:
        if character in _CJK_DIGITS:
            current = _CJK_DIGITS[character]
            continue
        unit = _CJK_UNITS[character]
        # A bare 十 means ten, e.g. 十二 = 12.
        total += (current or 1) * unit
        current = 0
    return total + current


@dataclass(frozen=True, slots=True)
class PageLabel:
    """A printed page number and where it sits in its own sequence."""

    printed_label: str
    scheme: NumberingScheme
    sequence: int | None
    sequence_ordinal: int | None = None

    def __post_init__(self) -> None:
        if self.scheme is not NumberingScheme.NONE and not self.printed_label:
            raise ValueError("a numbered label needs its printed text")
        if self.sequence is not None and self.sequence < 1:
            raise ValueError("printed page numbers start at 1")

    @property
    def is_numbered(self) -> bool:
        return self.scheme is not NumberingScheme.NONE and self.sequence is not None


@dataclass(frozen=True, slots=True)
class PageStructure:
    """Everything the pipeline knows about a page beyond its raster."""

    page_index: int
    role: PageRole = PageRole.UNKNOWN
    label: PageLabel = field(
        default_factory=lambda: PageLabel("", NumberingScheme.NONE, None)
    )
    #: Measured share of non-background pixels, when the raster could be read.
    #: The only evidence that separates "blank leaf" from "full-page illustration"
    #: without guessing (issues #87 and #74).
    ink_ratio: float | None = None

    def __post_init__(self) -> None:
        if self.ink_ratio is not None and not 0.0 <= self.ink_ratio <= 1.0:
            raise ValueError("ink_ratio must be between 0 and 1")

    @property
    def display_label(self) -> str:
        """What to show a reader: the printed number, else the physical one."""

        if self.label.printed_label:
            return self.label.printed_label
        return str(self.page_index + 1)


def parse_page_label(raw: str) -> PageLabel:
    """Interpret a margin string as a printed page number.

    Returns an unnumbered label rather than guessing: a footer reading "译林出版社"
    is not page 1, and mistaking it for one corrupts every downstream reference.
    """

    if raw is None:
        return PageLabel("", NumberingScheme.NONE, None)
    text = raw.strip().strip(_LABEL_NOISE).strip()
    if not text or len(text) > 12:
        return PageLabel("", NumberingScheme.NONE, None)

    if text.isdigit():
        return PageLabel(text, NumberingScheme.ARABIC, int(text))

    roman = parse_roman(text)
    # Front-matter roman numbering does not run into the hundreds, and mixed case
    # ("MiX") is a word or a running head rather than a folio. "mix" *is* a
    # canonical roman numeral (1009) but no book numbers a page that way.
    if roman is not None and 0 < roman <= 100 and text.strip().isupper() != (
        text.strip().islower()
    ):
        scheme = (
            NumberingScheme.ROMAN_UPPER
            if text.strip().isupper()
            else NumberingScheme.ROMAN_LOWER
        )
        return PageLabel(text.strip(), scheme, roman)

    cjk = parse_cjk_numeral(text)
    if cjk is not None and cjk > 0:
        return PageLabel(text, NumberingScheme.CJK, cjk)

    return PageLabel("", NumberingScheme.NONE, None)


@dataclass(frozen=True, slots=True)
class Chapter:
    """A node in the book's chapter tree.

    ``start_page_index`` is the physical page the chapter starts on, inclusive.
    ``level`` 1 is a part or a chapter title, 2 a section, 3 a subsection.
    """

    ordinal: int
    level: int
    title: str
    start_page_index: int
    printed_page: str = ""
    #: Where the title came from: the book's own table of contents (``toc``) or a
    #: heading found on the page (``heading``). A TOC entry is the author's own
    #: list; a heading is one page's typography. Not equally strong evidence.
    source: str = "heading"

    def __post_init__(self) -> None:
        if self.source not in ("toc", "heading", "manual"):
            raise ValueError("chapter source must be 'toc', 'heading' or 'manual'")
        if self.level < 1:
            raise ValueError("chapter level starts at 1")
        if self.start_page_index < 0:
            raise ValueError("chapter start page cannot be negative")
        if not self.title.strip():
            raise ValueError("chapter title must not be blank")


@dataclass(frozen=True, slots=True)
class BookStructure:
    """Page structure and chapters for one project."""

    pages: tuple[PageStructure, ...]
    chapters: tuple[Chapter, ...]
    #: How many entries the book's own contents page(s) listed. Kept on the
    #: structure because "the book lists 40 chapters and 36 could be located" is a
    #: fact about the book that an operator has to see, not a pipeline statistic.
    toc_entries: int = 0
    #: TOC titles that no page could be proved to match. Reported, never guessed.
    unplaced_toc_titles: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        indexes = [page.page_index for page in self.pages]
        if indexes != sorted(indexes):
            raise ValueError("page structure must be ordered by page index")
        if len(set(indexes)) != len(indexes):
            raise ValueError("page structure must not repeat a page index")
        starts = [chapter.start_page_index for chapter in self.chapters]
        if starts != sorted(starts):
            raise ValueError("chapters must be ordered by start page")
        ordinals = [chapter.ordinal for chapter in self.chapters]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("chapter ordinals must be unique")

    @property
    def is_empty(self) -> bool:
        return not self.pages and not self.chapters

    def page(self, page_index: int) -> PageStructure | None:
        for entry in self.pages:
            if entry.page_index == page_index:
                return entry
        return None

    def chapter_at(self, page_index: int) -> Chapter | None:
        """The deepest chapter that contains ``page_index``."""

        containing = [
            chapter
            for chapter in self.chapters
            if chapter.start_page_index <= page_index
        ]
        return containing[-1] if containing else None

    def page_range(self, chapter: Chapter) -> tuple[int, int]:
        """Inclusive physical page range covered by ``chapter``."""

        later = [
            other.start_page_index
            for other in self.chapters
            if other.start_page_index > chapter.start_page_index
        ]
        end = (min(later) - 1) if later else max(
            (page.page_index for page in self.pages), default=chapter.start_page_index
        )
        return chapter.start_page_index, max(chapter.start_page_index, end)

    def labels_by_page(self) -> Mapping[int, str]:
        """Display label per physical page, falling back to the physical index."""

        return MappingProxyType(
            {page.page_index: page.display_label for page in self.pages}
        )

    def printed_labels_by_page(self) -> Mapping[int, str]:
        """Printed labels only, omitting pages the book never numbered.

        Kept separate from :meth:`labels_by_page` because the fallback is a
        presentation choice: persisting "12" for an unnumbered plate would turn a
        display default into a claim about the book.
        """

        return MappingProxyType(
            {
                page.page_index: page.label.printed_label
                for page in self.pages
                if page.label.printed_label
            }
        )

    def blank_pages(self) -> tuple[int, ...]:
        return tuple(
            page.page_index for page in self.pages if page.role is PageRole.BLANK
        )


#: Ink coverage above which a textless page is a plate rather than a blank leaf.
#:
#: Measured on `benchmark/V..pdf` (562-page 300 dpi scan, reduced to 512 px before
#: counting): the book's blank separator leaves measure 0.39%, its sparsest pages
#: that *do* carry text 2.45% and 3.40%, and ordinary body pages 6-13%. Two percent
#: therefore separates "there is a picture or a page nobody managed to read" from
#: "there is a blank leaf" with margin on both sides.
PLATE_INK_RATIO = 0.02

#: Ink coverage below which a textless page has nothing to read at all, so the
#: measurement itself can confirm it as blank. Deliberately stricter than
#: ``PLATE_INK_RATIO``: on the same book it sits between the 0.39% blank leaf and
#: the 2.45% sparsest text page, which leaves room for a scanner's noise floor
#: without ever certifying a page that carries even one line of type.
BLANK_INK_RATIO = 0.01

#: A page dominated by something that is not paper (a coloured cover, a full-page
#: photograph) measures as almost entirely "ink". This book's front cover measures
#: 94%, which is why a first page like that is a cover whatever its caption says.
COVER_INK_RATIO = 0.5

#: Evidence for one page, as the whole-book classifier sees it.
@dataclass(frozen=True, slots=True)
class RoleEvidence:
    """What the classifier is allowed to know about a page.

    Only things an engine reported or a measurement produced: no filename hints,
    no position-in-the-book guesses beyond ordering.
    """

    page_index: int
    lines: tuple[str, ...] = ()
    label: PageLabel = field(
        default_factory=lambda: PageLabel("", NumberingScheme.NONE, None)
    )
    ink_ratio: float | None = None
    headings: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "".join(self.lines)

    @property
    def is_textual(self) -> bool:
        return bool(self.text.strip())


_COPYRIGHT_HINTS = (
    "isbn",
    "图书在版编目",
    "cip",
    "版权所有",
    "出版发行",
    "责任编辑",
    "印张",
    "插页",
    "开本",
    "字数",
    "印次",
    "印刷",
    "出版社",
    "出版者",
)
_TOC_HEADINGS = frozenset({"目录", "目次", "contents", "content", "table of contents"})
_TOC_LEADER = " .．·…・-—–_"
#: Punctuation that must not decide whether two titles are the same: OCR renders
#: the same comma differently on two passes, and quotation marks come in pairs.
_TEXT_NOISE = "，。、；：！？·…—–-.,;:!?《》〈〉\"'“”‘’()（）[]【】"


def _text_key(text: str) -> str:
    """Squash a string for comparison: no spaces, no punctuation variants."""

    return "".join(
        character
        for character in text.strip().lower()
        if not character.isspace() and character not in _TEXT_NOISE
    )


def _title_matches(title: str, headings: Iterable[str]) -> bool:
    """Whether a TOC title and a page heading say the same thing.

    Deliberately loose (containment either way after squashing) because OCR reads
    the same title twice through different paths; deliberately not fuzzy-scored,
    because a near-miss must stay unmapped rather than anchor a chapter wrongly.
    """

    key = _text_key(title)
    if len(key) < 2:
        return False
    for heading in headings:
        other = _text_key(heading)
        if not other:
            continue
        if key in other or other in key:
            return True
    return False


@dataclass(frozen=True, slots=True)
class TocEntry:
    """One line of the book's own table of contents."""

    title: str
    printed_label: str
    #: ``part`` | ``chapter`` | ``section`` | ``entry`` — inferred from the title's
    #: numbering pattern, which is the only structural hint a TOC line carries.
    kind: str = "entry"


@dataclass(frozen=True, slots=True)
class TocPage:
    """A page a TOC entry may point at: its printed label and its headings."""

    page_index: int
    label: PageLabel
    headings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TocMapping:
    """Chapters recovered from the TOC, plus the entries that could not be placed."""

    chapters: tuple[Chapter, ...] = ()
    unmapped: tuple[TocEntry, ...] = ()


def _number_words() -> str:
    return "〇零一二三四五六七八九十百千两0-9"


_PART_PATTERN = re.compile(rf"^第[{_number_words()}]{{1,4}}[卷部篇编]")
_CHAPTER_PATTERN = re.compile(rf"^第[{_number_words()}]{{1,4}}章")
_SECTION_PATTERN = re.compile(
    rf"^(第[{_number_words()}]{{1,4}}节|[{_number_words()}]{{1,4}}、)"
)
_APPENDIX_PATTERN = re.compile(r"^(附录|后记|译后记|跋|尾声|索引|致谢)")


def toc_entry_kind(title: str) -> str:
    """Classify a TOC title's numbering, or ``entry`` when it has none."""

    stripped = title.strip()
    if _PART_PATTERN.match(stripped):
        return "part"
    if _CHAPTER_PATTERN.match(stripped):
        return "chapter"
    if _SECTION_PATTERN.match(stripped):
        return "section"
    return "entry"


def parse_toc_entry(line: str) -> TocEntry | None:
    """Interpret one TOC line as ``title … printed page``, or reject it.

    Rejects more than it accepts on purpose. A body line that happens to end in a
    number ("…1923") would otherwise become a chapter titled after half a
    sentence, and a wrong TOC entry is worse than a missing one.
    """

    raw = line.strip()
    if not raw or len(raw) < 3:
        return None
    # The page number must be the last token, optionally behind leader dots.
    match = re.search(r"([0-9]{1,4}|[ivxlcdmIVXLCDM]{1,7}|[〇零一二三四五六七八九十百]{1,4})[.．·…・]?$", raw)
    if match is None:
        return None
    page_text = match.group(1)
    title = raw[: match.start()].strip().rstrip(_TOC_LEADER).strip()
    if len(title) < 2 or len(title) > 60:
        return None
    label = parse_page_label(page_text)
    if not label.is_numbered:
        return None
    # A title made only of digits or punctuation is a table rule, not a chapter.
    if not any(character.isalpha() or "\u4e00" <= character <= "\u9fff" for character in title):
        return None
    # The page number must be separated from the title, or "1923年" style prose
    # tails become folios.
    gap = raw[match.start() :].lstrip()
    if gap and not any(character in _TOC_LEADER or character.isspace() for character in raw[match.start() - 1 : match.start()]):
        return None
    return TocEntry(title=title, printed_label=page_text, kind=toc_entry_kind(title))


def parse_toc_entries(lines: Iterable[str]) -> tuple[TocEntry, ...]:
    """Every parseable entry of one TOC page, in printed order."""

    entries: list[TocEntry] = []
    for line in lines:
        entry = parse_toc_entry(line)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def looks_like_toc_page(lines: Iterable[str]) -> bool:
    """Whether a page is a table of contents.

    Two independent shapes qualify: an explicit "目录/Contents" heading, or a page
    where at least three lines parse as *title … page number* and those lines are
    the majority. Body pages fail both: they rarely end lines with a bare number,
    and they have no such heading.
    """

    materialised = [line for line in lines if line.strip()]
    if not materialised:
        return False
    for line in materialised[:3]:
        if _text_key(line) in _TOC_HEADINGS:
            return True
    entries = sum(1 for line in materialised if parse_toc_entry(line) is not None)
    return entries >= 3 and entries * 2 >= len(materialised)


def detect_page_roles(
    page_index: int,
    *,
    text: str,
    ink_ratio: float | None = None,
) -> PageRole:
    """Classify a single page without book context.

    Only the two roles that need no context are decided here: a page with no text
    is blank or a plate. Everything else (cover, copyright, TOC, body span) needs
    the whole book and is decided by :func:`classify_page_roles`.
    """

    if text.strip():
        return PageRole.UNKNOWN
    if ink_ratio is not None and ink_ratio >= PLATE_INK_RATIO:
        return PageRole.PLATE
    return PageRole.BLANK


def _body_run(pages: Sequence[RoleEvidence]) -> tuple[int, int] | None:
    """Physical span of the longest run of printed page numbers.

    A book numbers its body in one continuous run; front matter and back matter
    restart at 1 or use another scheme. The longest run is therefore the body, and
    its span is what separates front matter from body from back matter.
    """

    runs: dict[int, list[int]] = {}
    for page in pages:
        ordinal = page.label.sequence_ordinal
        if ordinal is None:
            continue
        runs.setdefault(ordinal // 1_000_000, []).append(page.page_index)
    if not runs:
        return None
    # Longest run wins; ties go to the later run, because a body that restarts
    # numbering sits after the front matter that also restarted it.
    chosen = max(runs.items(), key=lambda item: (len(item[1]), item[0]))[1]
    return min(chosen), max(chosen)


def classify_page_roles(pages: Sequence[RoleEvidence]) -> Mapping[int, PageRole]:
    """Classify every page of a book from its own evidence.

    Roles drive export decisions, so a page whose evidence does not clearly say
    what it is stays ``UNKNOWN`` — an unclassified page is exported as text, which
    is the safe failure. Nothing here looks at file names or expected layouts.
    """

    ordered = sorted(pages, key=lambda page: page.page_index)
    textual = [page for page in ordered if page.is_textual]
    first_textual = textual[0].page_index if textual else None
    span = _body_run(ordered)
    roles: dict[int, PageRole] = {}

    for page in ordered:
        if not page.is_textual:
            roles[page.page_index] = detect_page_roles(
                page.page_index, text=page.text, ink_ratio=page.ink_ratio
            )
            continue
        lowered = page.text.lower()
        hints = sum(1 for hint in _COPYRIGHT_HINTS if hint in lowered)
        if hints >= 2 and not page.label.is_numbered:
            roles[page.page_index] = PageRole.COPYRIGHT
            continue
        if looks_like_toc_page(page.lines):
            roles[page.page_index] = PageRole.TABLE_OF_CONTENTS
            continue
        if (
            page.page_index == first_textual
            and not page.label.is_numbered
            and not page.headings
            and (
                len(page.text.strip()) <= 40
                or (page.ink_ratio is not None and page.ink_ratio >= COVER_INK_RATIO)
            )
        ):
            # A cover carries a short title block and no folio. A first page that
            # is long, numbered or has a heading is body text, not a cover: some
            # scans start mid-book.
            roles[page.page_index] = PageRole.COVER
            continue
        if span is None:
            roles[page.page_index] = PageRole.UNKNOWN
            continue
        start, end = span
        if page.page_index < start:
            roles[page.page_index] = PageRole.FRONT_MATTER
        elif page.page_index <= end:
            roles[page.page_index] = PageRole.BODY
        else:
            roles[page.page_index] = PageRole.BACK_MATTER
    return MappingProxyType(roles)


def map_toc_chapters(
    entries: Sequence[TocEntry],
    pages: Sequence[TocPage],
) -> TocMapping:
    """Anchor TOC entries to physical pages, using two independent signals.

    A printed page number alone is ambiguous on purpose in this kind of book: three
    sections each number from 1, so "1" names three different physical pages. An
    entry is therefore only accepted when the *page it points at carries a heading
    that matches the entry's title* — the label narrows it down, the page's own
    typography confirms it. Everything else is reported as unmapped rather than
    guessed, and the caller falls back to per-page heading detection.
    """

    by_label: dict[tuple[NumberingScheme, int], list[TocPage]] = {}
    for page in pages:
        if page.label.is_numbered and page.label.sequence is not None:
            by_label.setdefault(
                (page.label.scheme, page.label.sequence), []
            ).append(page)

    chapters: list[Chapter] = []
    unmapped: list[TocEntry] = []
    used: set[int] = set()
    previous = -1
    has_part = any(entry.kind == "part" for entry in entries)
    ordinal = 0

    for entry in entries:
        label = parse_page_label(entry.printed_label)
        if label.sequence is None:
            unmapped.append(entry)
            continue
        candidates = [
            page
            for page in by_label.get((label.scheme, label.sequence), [])
            if page.page_index not in used and page.headings
        ]
        matched = [
            page for page in candidates if _title_matches(entry.title, page.headings)
        ]
        pool = matched or (candidates if len(candidates) == 1 else [])
        if not pool:
            unmapped.append(entry)
            continue
        ahead = [page for page in pool if page.page_index > previous]
        chosen = min(ahead or pool, key=lambda page: page.page_index)
        used.add(chosen.page_index)
        previous = chosen.page_index
        chapters.append(
            Chapter(
                ordinal=ordinal,
                level=_entry_level(entry.kind, has_part),
                title=entry.title,
                start_page_index=chosen.page_index,
                printed_page=entry.printed_label,
                source="toc",
            )
        )
        ordinal += 1

    # A contents page can list an appendix before a chapter that precedes it in the
    # book, and its own order is not guaranteed to be the physical order. The tree
    # must be ordered by page, so sort and renumber rather than trusting the page.
    chapters.sort(key=lambda chapter: chapter.start_page_index)
    ordered = tuple(
        Chapter(
            ordinal=index,
            level=chapter.level,
            title=chapter.title,
            start_page_index=chapter.start_page_index,
            printed_page=chapter.printed_page,
            source=chapter.source,
        )
        for index, chapter in enumerate(chapters)
    )
    return TocMapping(chapters=ordered, unmapped=tuple(unmapped))


def _entry_level(kind: str, has_part: bool) -> int:
    if kind == "part":
        return 1
    if kind == "section":
        return 3 if has_part else 2
    return 2 if has_part else 1


def assign_sequences(
    candidates: Iterable[tuple[int, PageLabel]],
    *,
    max_gap: int = 3,
    min_run_length: int = 2,
) -> Mapping[int, PageLabel]:
    """Place detected labels into monotone sequences.

    A new sequence starts when a number goes backwards (the printed numbering
    restarted) or jumps further than ``max_gap`` (a misread). Labels that cannot
    be placed monotonically are dropped: a wrong folio is worse than none, because
    every citation and cross-reference built on it is then wrong too.

    A sequence seen on only one page is dropped as well. A single numeric margin
    line is exactly what a running head like "V." (this book's own title) looks
    like, and one detection carries no evidence that it is a sequence at all.
    """

    ordered = sorted(candidates, key=lambda item: item[0])
    result: dict[int, PageLabel] = {}
    scheme: NumberingScheme | None = None
    last: int | None = None
    ordinal = 0
    # A stored label keeps the printed text; the ordinal records which run of
    # numbering it belongs to, so 5 in section two is distinct from 5 in section one.
    run = 1

    for page_index, label in ordered:
        if not label.is_numbered or label.sequence is None:
            continue
        if scheme is None or label.scheme is not scheme:
            scheme = label.scheme
            run += 1 if last is not None else 0
            last = label.sequence
            ordinal = 1
            result[page_index] = _with_ordinal(label, run, ordinal)
            continue
        if last is None:
            last = label.sequence
            ordinal = 1
            result[page_index] = _with_ordinal(label, run, ordinal)
            continue
        step = label.sequence - last
        if 1 <= step <= max_gap:
            ordinal += step
            last = label.sequence
            result[page_index] = _with_ordinal(label, run, ordinal)
            continue
        if step <= 0:
            # Numbering restarted: a new numbered section.
            run += 1
            ordinal = 1
            last = label.sequence
            result[page_index] = _with_ordinal(label, run, ordinal)
            continue
        # Too large a jump: treat the detection as a misread and drop it.

    counts: dict[int, int] = {}
    for label in result.values():
        if label.sequence_ordinal is not None:
            run_number = label.sequence_ordinal // 1_000_000
            counts[run_number] = counts.get(run_number, 0) + 1
    return MappingProxyType(
        {
            page_index: label
            for page_index, label in result.items()
            if label.sequence_ordinal is not None
            and counts.get(label.sequence_ordinal // 1_000_000, 0) >= min_run_length
        }
    )


def _with_ordinal(label: PageLabel, run: int, ordinal: int) -> PageLabel:
    return PageLabel(
        printed_label=label.printed_label,
        scheme=label.scheme,
        sequence=label.sequence,
        sequence_ordinal=run * 1_000_000 + ordinal,
    )


_HEADING_PATTERNS = (
    re.compile(r"^第[〇零一二三四五六七八九十百千0-9]+[章节回卷篇部]"),
    re.compile(r"^[〇零一二三四五六七八九十]+[、.．]"),
    re.compile(r"^(序|序言|前言|引言|导言|后记|附录|译后记|跋|楔子|尾声)([　\s]|$)"),
)


def looks_like_heading(text: str) -> bool:
    """Structural hints that a short line is a heading rather than prose."""

    stripped = text.strip()
    if not stripped or len(stripped) > 30:
        return False
    if stripped.endswith(("。", "，", "；", "：", "！", "？", ",", ".", ";", ":")):
        return False
    return any(pattern.match(stripped) for pattern in _HEADING_PATTERNS)
