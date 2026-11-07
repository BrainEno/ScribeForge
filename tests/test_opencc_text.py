from scribeforge.adapters.opencc_text import opencc_simplifier
from scribeforge.domain.content import VerifiedBook, VerifiedPage
from scribeforge.domain.export_normalization import (
    NormalizationPolicy,
    derive_normalized_book,
)


def test_opencc_t2s_adapter_performs_real_simplified_derivation() -> None:
    converter, name = opencc_simplifier()
    book = VerifiedBook("Title", (VerifiedPage(0, ("繁體漢字與異體字",)),))

    derived = derive_normalized_book(
        book,
        NormalizationPolicy.SIMPLIFIED,
        converter=converter,
        converter_name=name,
    )

    assert name == "opencc:t2s"
    assert derived.book.pages[0].lines == ("繁体汉字与异体字",)
    assert book.pages[0].lines == ("繁體漢字與異體字",)
    assert derived.report.changes
    assert all(change.rule == "opencc:t2s" for change in derived.report.changes)
