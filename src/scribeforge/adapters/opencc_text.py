"""OpenCC adapter kept outside the pure export-normalization domain."""

from __future__ import annotations

from collections.abc import Callable

from opencc import OpenCC


def opencc_simplifier() -> tuple[Callable[[str], str], str]:
    """Return the character-level Traditional-to-Simplified OpenCC converter."""

    opencc = OpenCC("t2s")

    def convert(text: str) -> str:
        converted: str = opencc.convert(text)
        return converted

    return convert, "opencc:t2s"
