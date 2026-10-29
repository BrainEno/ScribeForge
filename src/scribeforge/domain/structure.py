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
from collections.abc import Iterable, Mapping
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

    def __post_init__(self) -> None:
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


def detect_page_roles(
    page_index: int,
    *,
    text: str,
    ink_ratio: float | None = None,
) -> PageRole:
    """Classify a page from its accepted text and ink coverage.

    Conservative by design: an unrecognised page becomes ``UNKNOWN`` rather than
    being forced into a role it may not have, because roles drive export
    decisions (a cover must not be typeset as body text).
    """

    stripped = text.strip()
    if not stripped:
        # No text at all: either a blank leaf or a full-page illustration.
        if ink_ratio is not None and ink_ratio > 0.05:
            return PageRole.PLATE
        return PageRole.BLANK
    if page_index == 0:
        return PageRole.COVER
    return PageRole.UNKNOWN


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
