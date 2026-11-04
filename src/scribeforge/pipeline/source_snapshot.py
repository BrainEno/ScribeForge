"""A verified, private source-file snapshot for a resumable import."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from scribeforge.domain.cancellation import raise_if_cancelled
from scribeforge.domain.page_asset import file_sha256


@contextmanager
def verified_source_snapshot(source: Path, expected_sha256: str) -> Iterator[Path]:
    """Copy a source and verify the bytes against the project's recorded identity."""
    with tempfile.TemporaryDirectory(prefix="scribeforge-source-") as directory:
        snapshot = Path(directory) / source.name
        digest = hashlib.sha256()
        with source.open("rb") as input_file, snapshot.open("xb") as output_file:
            while chunk := input_file.read(1024 * 1024):
                raise_if_cancelled()
                digest.update(chunk)
                output_file.write(chunk)
        raise_if_cancelled()
        if digest.hexdigest() != expected_sha256 or file_sha256(snapshot) != expected_sha256:
            raise ValueError("source SHA-256 changed while preparing import; restore the recorded source")
        yield snapshot
