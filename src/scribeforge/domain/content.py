from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VerifiedPage:
    """One verified page: its source index and accepted lines in reading order."""

    page_index: int
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VerifiedBook:
    """The accepted text of a project, assembled only from recorded decisions.

    This model never carries OCR candidate text: every line is the newest
    accepted decision for its pair, so export cannot silently fall back to raw
    engine output.
    """

    title: str
    pages: tuple[VerifiedPage, ...]
