"""Page rasters: the digest that proves a page is still the page that was read.

A project records the SHA-256 of the file it was imported from, and until v11 it
recorded nothing about the rasters it rendered *from* that file. So a page image
could be replaced, re-rendered at another DPI, or truncated, and every later stage
would happily read the new pixels while the store still described the old ones.
The audit only asked whether the file existed.

This module is the one place that answers "are these still the bytes we recorded?":

* import computes the digest, the byte length, the render parameters and the
  evidence version, and stores them with the page (schema v11);
* the orchestrator re-checks the raster before OCR reads it, and before a review
  crop is cut, so a decision can never be made from a raster nobody accounted for;
* export re-checks it before a cover or plate is embedded;
* the audit reports every page whose digest is missing, mismatched or unreadable.

A missing digest is *not* an error. Projects imported before v11 have none, and
refusing to open them would trade one silent failure for a loud one. The audit says
so and `scribeforge rehash-pages` fills the gap by measuring the file — it never
overwrites a digest that exists, because doing so would let a changed page launder
itself through the repair command.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

#: Version of the "what a page raster is" contract. Bump it when the renderer, the
#: preprocessing defaults or the meaning of the stored parameters changes, so a
#: later reader can tell two eras of evidence apart without guessing from dates.
PAGE_ASSET_VERSION = 1

_READ_CHUNK = 1 << 20


class PageAssetError(ValueError):
    """Raised when a page raster is not the raster the project recorded."""


def file_sha256(path: Path) -> str:
    """SHA-256 of a file, streamed so a 40 MB scan does not sit in memory twice."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PageAssetEvidence:
    """What was recorded about one rendered page image.

    ``sha256`` is the whole truth of *content*; ``byte_size`` is a cheap second
    signal that also catches truncation; ``render_parameters`` says how the bytes
    were produced (DPI, renderer, extension) so two rasters that differ can be
    explained rather than merely detected.
    """

    sha256: str
    byte_size: int
    render_parameters: Mapping[str, Any] = field(default_factory=dict)
    version: int = PAGE_ASSET_VERSION

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("page asset digest must be lowercase SHA-256 hex")
        if self.byte_size <= 0:
            raise ValueError("page asset size must be positive")
        if self.version < 1:
            raise ValueError("page asset evidence version must be positive")

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        render_parameters: Mapping[str, Any] | None = None,
        version: int = PAGE_ASSET_VERSION,
    ) -> PageAssetEvidence:
        """Evidence for bytes that are already in hand (the importer's case)."""

        if not data:
            raise ValueError("a rendered page must carry bytes")
        return cls(
            sha256=hashlib.sha256(data).hexdigest(),
            byte_size=len(data),
            render_parameters=MappingProxyType(dict(render_parameters or {})),
            version=version,
        )

    def as_render_parameters(self) -> dict[str, Any]:
        """The parameters as a plain dict, ready to be stored as JSON."""

        return dict(self.render_parameters)


def verify_page_asset(
    path: Path,
    evidence: PageAssetEvidence | None,
    *,
    what: str,
) -> str:
    """Check a raster against its recorded evidence and return its digest.

    ``evidence is None`` means the project predates v11: the digest is computed and
    returned so the caller can report it, but nothing is refused. Refusing there
    would make every old project unusable, which is a worse outcome than a warning
    the audit already prints.
    """

    if not path.is_file():
        raise PageAssetError(f"{what} is missing at {path}")
    try:
        actual = file_sha256(path)
    except OSError as exc:
        raise PageAssetError(f"{what} at {path} is unreadable: {exc.strerror or exc}") from exc
    if evidence is None:
        return actual
    if actual != evidence.sha256:
        raise PageAssetError(
            f"{what} at {path} is not the raster this project recorded "
            f"(recorded {evidence.sha256[:12]}…, found {actual[:12]}…); "
            "restore it from a backup or re-import the project"
        )
    return actual


def verify_recorded_bytes(
    data: bytes,
    evidence: PageAssetEvidence,
    *,
    what: str,
) -> None:
    """Check bytes that are already in memory (export writes them from here)."""

    actual = hashlib.sha256(data).hexdigest()
    if actual != evidence.sha256:
        raise PageAssetError(
            f"{what} no longer matches the raster this project recorded "
            f"(recorded {evidence.sha256[:12]}…, found {actual[:12]}…)"
        )
