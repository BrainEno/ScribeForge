from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from scribeforge.domain.cancellation import (
    CancellationToken,
    ProcessingCancelled,
    cancellation_scope,
)
from scribeforge.pipeline.source_snapshot import verified_source_snapshot


def test_snapshot_copies_exact_bytes_and_cleans_up(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    content = b"source data" * 100
    source.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    with verified_source_snapshot(source, digest) as snapshot:
        assert snapshot != source
        assert snapshot.suffix == ".pdf"
        assert snapshot.read_bytes() == content
        source.write_bytes(b"different")
        assert snapshot.read_bytes() == content
    assert not snapshot.exists()
    assert source.read_bytes() == b"different"


def test_snapshot_refuses_hash_mismatch_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(b"changed")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    with (
        pytest.raises(ValueError, match="source SHA-256 changed"),
        verified_source_snapshot(source, hashlib.sha256(b"original").hexdigest()),
    ):
        pytest.fail("invalid snapshot must not reach consumer")
    assert not list(scratch.iterdir())
    assert source.read_bytes() == b"changed"


def test_snapshot_cancellation_cleans_up_without_mutating_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.pdf"
    data = b"A" * 100
    source.write_bytes(data)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    token = CancellationToken()
    token.cancel()
    with (
        cancellation_scope(token),
        pytest.raises(ProcessingCancelled),
        verified_source_snapshot(source, hashlib.sha256(data).hexdigest()),
    ):
        pytest.fail("cancelled snapshot must not reach consumer")
    assert not list(scratch.iterdir())
    assert source.read_bytes() == data
