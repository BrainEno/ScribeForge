from __future__ import annotations

import hashlib
import json

import pytest

from scribeforge.domain.content import (
    PageImageRole,
    VerifiedBook,
    VerifiedChapter,
    VerifiedImage,
    VerifiedPage,
)
from scribeforge.domain.export_normalization import (
    NormalizationPolicy,
    derive_normalized_book,
    normalization_report_json,
)


def _book() -> VerifiedBook:
    return VerifiedBook(
        "Source title",
        (VerifiedPage(0, ("𠮷髙峯", "保持原字")),),
        chapters=(VerifiedChapter("羣峰", 1, 0),),
    )


def test_preserve_is_the_zero_change_default_and_returns_the_same_book() -> None:
    book = _book()

    derived = derive_normalized_book(book)

    assert derived.book is book
    assert derived.report.policy is NormalizationPolicy.PRESERVE
    assert derived.report.converter == "none"
    assert derived.report.changes == ()
    assert derived.report.source_sha256 == derived.report.normalized_sha256


def test_canonical_variants_derive_text_without_mutating_accepted_content() -> None:
    book = _book()

    derived = derive_normalized_book(book, NormalizationPolicy.CANONICAL_VARIANTS)

    assert derived.book.pages[0].lines == ("吉高峰", "保持原字")
    assert derived.book.chapters[0].title == "群峰"
    assert book.pages[0].lines == ("𠮷髙峯", "保持原字")
    assert book.chapters[0].title == "羣峰"
    assert derived.report.source_sha256 != derived.report.normalized_sha256
    line_changes = [change for change in derived.report.changes if change.location == "line"]
    assert [(change.original, change.normalized) for change in line_changes] == [
        ("𠮷", "吉"),
        ("髙", "高"),
        ("峯", "峰"),
    ]
    assert [change.source_start for change in line_changes] == [0, 1, 2]
    assert all(change.page_index == 0 and change.line_index == 0 for change in line_changes)
    assert all(change.rule == "canonical_variants:v1" for change in line_changes)
    chapter_change = next(
        change for change in derived.report.changes if change.location == "chapter"
    )
    assert chapter_change.chapter_index == 0
    assert (chapter_change.original, chapter_change.normalized) == ("羣", "群")


def test_simplified_uses_the_explicit_converter_and_records_changed_spans() -> None:
    def convert(text: str) -> str:
        return text.replace("繁體", "简体")

    book = VerifiedBook("Title", (VerifiedPage(3, ("前繁體後",)),))

    derived = derive_normalized_book(
        book,
        NormalizationPolicy.SIMPLIFIED,
        converter=convert,
        converter_name="test:t2s",
    )

    assert derived.book.pages[0].lines == ("前简体後",)
    change = derived.report.changes[0]
    assert (change.original, change.normalized) == ("繁體", "简体")
    assert (change.source_start, change.source_end) == (1, 3)
    assert (change.normalized_start, change.normalized_end) == (1, 3)
    assert derived.report.converter == "test:t2s"
    assert change.rule == "test:t2s"


def test_simplified_refuses_to_guess_when_no_converter_is_provided() -> None:
    with pytest.raises(ValueError, match="converter"):
        derive_normalized_book(_book(), NormalizationPolicy.SIMPLIFIED)


def test_figure_caption_is_derived_with_its_accepted_line() -> None:
    payload = b"png-like-image"
    book = VerifiedBook(
        "Title",
        (VerifiedPage(0, ("峯",)),),
        images=(
            VerifiedImage(
                0,
                "plate.png",
                "image/png",
                PageImageRole.FIGURE,
                hashlib.sha256(payload).hexdigest(),
                payload,
                caption="峯",
            ),
        ),
    )

    derived = derive_normalized_book(book, NormalizationPolicy.CANONICAL_VARIANTS)

    assert derived.book.pages[0].lines == ("峰",)
    assert derived.book.images[0].caption == "峰"
    assert derived.book.images[0].data == payload


def test_report_json_is_deterministic_and_contains_all_change_coordinates() -> None:
    derived = derive_normalized_book(_book(), NormalizationPolicy.CANONICAL_VARIANTS)

    encoded = normalization_report_json(derived.report)
    document = json.loads(encoded)

    assert encoded == normalization_report_json(derived.report)
    assert document["schema_version"] == 1
    assert document["policy"] == "canonical_variants"
    assert document["change_count"] == 4
    assert document["changes"][0] == {
        "location": "line",
        "page_index": 0,
        "line_index": 0,
        "chapter_index": None,
        "source_start": 0,
        "source_end": 1,
        "normalized_start": 0,
        "normalized_end": 1,
        "original": "𠮷",
        "normalized": "吉",
        "rule": "canonical_variants:v1",
    }
