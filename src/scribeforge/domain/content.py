from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerifiedPage:
    """One verified page: its source index and accepted lines in reading order.

    ``printed_label`` is the page number printed in the book, which is *not* the
    physical index: this book restarts numbering per section and leaves some pages
    unnumbered. Export shows the printed label to readers and keeps the physical
    index for navigation and evidence references.
    """

    page_index: int
    lines: tuple[str, ...]
    printed_label: str = ""


@dataclass(frozen=True, slots=True)
class VerifiedChapter:
    """A chapter as it appears to a reader, anchored to a physical page."""

    title: str
    level: int
    page_index: int
    printed_page: str = ""


@dataclass(frozen=True, slots=True)
class VerifiedBook:
    """The accepted text of a project, assembled only from recorded decisions.

    This model never carries OCR candidate text: every line is the newest
    accepted decision for its pair, so export cannot silently fall back to raw
    engine output.
    """

    title: str
    pages: tuple[VerifiedPage, ...]
    chapters: tuple[VerifiedChapter, ...] = ()
