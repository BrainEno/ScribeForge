"""Explicit, auditable text derivation at the export boundary.

This module is deliberately separate from :mod:`scribeforge.domain.normalize`,
whose folding rules are comparison keys and must never become exported text.
Accepted decisions and source OCR remain untouched; callers receive a new book
plus a deterministic report of every changed character or span.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from enum import Enum

from scribeforge.domain.content import VerifiedBook

_CANONICAL_VARIANTS = {
    "𠮷": "吉",
    "髙": "高",
    "峯": "峰",
    "羣": "群",
    "祕": "秘",
    "歎": "嘆",
}


class NormalizationPolicy(str, Enum):
    PRESERVE = "preserve"
    CANONICAL_VARIANTS = "canonical_variants"
    SIMPLIFIED = "simplified"


@dataclass(frozen=True, slots=True)
class NormalizationChange:
    location: str
    page_index: int
    line_index: int | None
    chapter_index: int | None
    source_start: int
    source_end: int
    normalized_start: int
    normalized_end: int
    original: str
    normalized: str
    rule: str

    def to_dict(self) -> dict[str, object]:
        return {
            "location": self.location,
            "page_index": self.page_index,
            "line_index": self.line_index,
            "chapter_index": self.chapter_index,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "normalized_start": self.normalized_start,
            "normalized_end": self.normalized_end,
            "original": self.original,
            "normalized": self.normalized,
            "rule": self.rule,
        }


@dataclass(frozen=True, slots=True)
class NormalizationReport:
    policy: NormalizationPolicy
    converter: str
    source_sha256: str
    normalized_sha256: str
    changes: tuple[NormalizationChange, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "policy": self.policy.value,
            "converter": self.converter,
            "source_sha256": self.source_sha256,
            "normalized_sha256": self.normalized_sha256,
            "change_count": len(self.changes),
            "changes": [change.to_dict() for change in self.changes],
        }


@dataclass(frozen=True, slots=True)
class NormalizedBook:
    book: VerifiedBook
    report: NormalizationReport


def _text_digest(book: VerifiedBook) -> str:
    document = {
        "title": book.title,
        "pages": [
            {"page_index": page.page_index, "lines": list(page.lines)}
            for page in book.pages
        ],
        "chapters": [
            {"title": chapter.title, "page_index": chapter.page_index}
            for chapter in book.chapters
        ],
        "image_captions": [
            {"page_index": image.page_index, "caption": image.caption}
            for image in book.images
        ],
    }
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _change(
    *,
    location: str,
    page_index: int,
    line_index: int | None,
    chapter_index: int | None,
    source_start: int,
    source_end: int,
    normalized_start: int,
    normalized_end: int,
    original: str,
    normalized: str,
    rule: str,
) -> NormalizationChange:
    return NormalizationChange(
        location,
        page_index,
        line_index,
        chapter_index,
        source_start,
        source_end,
        normalized_start,
        normalized_end,
        original,
        normalized,
        rule,
    )


def _canonical_text(
    text: str,
    *,
    location: str,
    page_index: int,
    line_index: int | None = None,
    chapter_index: int | None = None,
) -> tuple[str, list[NormalizationChange]]:
    characters: list[str] = []
    changes: list[NormalizationChange] = []
    for index, character in enumerate(text):
        normalized = _CANONICAL_VARIANTS.get(character, character)
        characters.append(normalized)
        if normalized != character:
            changes.append(
                _change(
                    location=location,
                    page_index=page_index,
                    line_index=line_index,
                    chapter_index=chapter_index,
                    source_start=index,
                    source_end=index + 1,
                    normalized_start=index,
                    normalized_end=index + 1,
                    original=character,
                    normalized=normalized,
                    rule="canonical_variants:v1",
                )
            )
    return "".join(characters), changes


def _converted_text(
    text: str,
    *,
    converter: Callable[[str], str],
    converter_name: str,
    location: str,
    page_index: int,
    line_index: int | None = None,
    chapter_index: int | None = None,
) -> tuple[str, list[NormalizationChange]]:
    normalized = converter(text)
    if not isinstance(normalized, str):
        raise TypeError("a text normalization converter must return str")
    changes: list[NormalizationChange] = []
    matcher = SequenceMatcher(None, text, normalized, autojunk=False)
    for operation, source_start, source_end, normalized_start, normalized_end in matcher.get_opcodes():
        if operation == "equal":
            continue
        changes.append(
            _change(
                location=location,
                page_index=page_index,
                line_index=line_index,
                chapter_index=chapter_index,
                source_start=source_start,
                source_end=source_end,
                normalized_start=normalized_start,
                normalized_end=normalized_end,
                original=text[source_start:source_end],
                normalized=normalized[normalized_start:normalized_end],
                rule=converter_name,
            )
        )
    return normalized, changes


def derive_normalized_book(
    book: VerifiedBook,
    policy: NormalizationPolicy | str = NormalizationPolicy.PRESERVE,
    *,
    converter: Callable[[str], str] | None = None,
    converter_name: str = "opencc:t2s",
) -> NormalizedBook:
    """Return an export-only derivation; never mutate the accepted source book."""

    selected = NormalizationPolicy(policy)
    source_sha256 = _text_digest(book)
    if selected is NormalizationPolicy.PRESERVE:
        return NormalizedBook(
            book,
            NormalizationReport(
                selected, "none", source_sha256, source_sha256, ()
            ),
        )
    if selected is NormalizationPolicy.SIMPLIFIED and converter is None:
        raise ValueError("simplified normalization requires an explicit converter")

    changes: list[NormalizationChange] = []
    pages = []
    for page in book.pages:
        lines: list[str] = []
        for line_index, line in enumerate(page.lines):
            if selected is NormalizationPolicy.CANONICAL_VARIANTS:
                normalized, line_changes = _canonical_text(
                    line,
                    location="line",
                    page_index=page.page_index,
                    line_index=line_index,
                )
            else:
                assert converter is not None
                normalized, line_changes = _converted_text(
                    line,
                    converter=converter,
                    converter_name=converter_name,
                    location="line",
                    page_index=page.page_index,
                    line_index=line_index,
                )
            lines.append(normalized)
            changes.extend(line_changes)
        pages.append(replace(page, lines=tuple(lines)))

    chapters = []
    for chapter_index, chapter in enumerate(book.chapters):
        if selected is NormalizationPolicy.CANONICAL_VARIANTS:
            title, chapter_changes = _canonical_text(
                chapter.title,
                location="chapter",
                page_index=chapter.page_index,
                chapter_index=chapter_index,
            )
        else:
            assert converter is not None
            title, chapter_changes = _converted_text(
                chapter.title,
                converter=converter,
                converter_name=converter_name,
                location="chapter",
                page_index=chapter.page_index,
                chapter_index=chapter_index,
            )
        chapters.append(replace(chapter, title=title))
        changes.extend(chapter_changes)

    images = []
    for image in book.images:
        if selected is NormalizationPolicy.CANONICAL_VARIANTS:
            caption, image_changes = _canonical_text(
                image.caption,
                location="image",
                page_index=image.page_index,
            )
        else:
            assert converter is not None
            caption, image_changes = _converted_text(
                image.caption,
                converter=converter,
                converter_name=converter_name,
                location="image",
                page_index=image.page_index,
            )
        images.append(replace(image, caption=caption))
        changes.extend(image_changes)

    normalized_book = replace(
        book,
        pages=tuple(pages),
        chapters=tuple(chapters),
        images=tuple(images),
    )
    report = NormalizationReport(
        selected,
        (
            "canonical_variants:v1"
            if selected is NormalizationPolicy.CANONICAL_VARIANTS
            else converter_name
        ),
        source_sha256,
        _text_digest(normalized_book),
        tuple(changes),
    )
    return NormalizedBook(normalized_book, report)


def normalization_report_json(
    report: NormalizationReport,
    *,
    output_file: str | None = None,
    output_sha256: str | None = None,
) -> str:
    if (output_file is None) != (output_sha256 is None):
        raise ValueError("output file and digest must be recorded together")
    document = report.to_dict()
    if output_file is not None and output_sha256 is not None:
        document["output"] = {"file": output_file, "sha256": output_sha256}
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
