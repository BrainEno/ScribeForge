from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import scripts.compile_runtime_locks as lock_compiler

WINDOWS_HASH = "8f11ffae31ec67fc2554c2ef891dcb561dae9a2a3ed941f9e134c2db06657dbc"
LINUX_HASH = "3df850c2f8db9934de4cf8f9152f8dc2558f49f298f37f90c517e8e5c84c30e9"
MAC_HASH = "2fe67f6c014c84f655ee06f6f66213f9254b3a8b6bda6cda0ccd4232c73c06f0"
CONTINUATION = chr(92)


def test_augment_pypi_hashes_preserves_custom_and_adds_official_artifacts() -> None:
    body = (
        f"protobuf==6.33.4 {CONTINUATION}\n"
        f"    --hash=sha256:{WINDOWS_HASH}\n"
        "    # via paddlepaddle\n"
        f"custom-only==1.0 {CONTINUATION}\n"
        f"    --hash=sha256:{'a' * 64}\n"
        "    # via paddle\n"
    )

    def official_digests(name: str, version: str) -> frozenset[str]:
        assert version in {"6.33.4", "1.0"}
        if name == "custom-only":
            return frozenset()
        return frozenset({LINUX_HASH, MAC_HASH})

    result = lock_compiler.augment_pypi_hashes(body, official_digests)

    assert result.index(WINDOWS_HASH) < result.index("custom-only==")
    assert result.index(LINUX_HASH) < result.index("custom-only==")
    assert result.index(MAC_HASH) < result.index("custom-only==")
    assert "    # via paddlepaddle\n" in result
    assert f"custom-only==1.0 {CONTINUATION}\n    --hash=sha256:{'a' * 64}\n" in result


def test_compile_lock_merges_official_hashes_after_uv_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    name = "paddleocr-cpu-py312-universal.txt"
    (source_dir / name.replace(".txt", ".in")).write_text("protobuf==6.33.4\n", encoding="utf-8")
    monkeypatch.setattr(lock_compiler, "ROOT", tmp_path)
    monkeypatch.setattr(lock_compiler, "SOURCE_DIR", source_dir)
    monkeypatch.setattr(
        lock_compiler,
        "_pypi_digests",
        lambda name, version: frozenset({LINUX_HASH}),
    )

    def fake_compile(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        output = Path(argv[argv.index("--output-file") + 1])
        output.write_text(
            f"protobuf==6.33.4 {CONTINUATION}\n    --hash=sha256:{WINDOWS_HASH}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(lock_compiler.subprocess, "run", fake_compile)

    result = lock_compiler.compile_lock("uv", name)

    assert WINDOWS_HASH in result
    assert LINUX_HASH in result


def test_cpu_lock_contains_official_linux_artifacts_required_by_real_install() -> None:
    lock = (
        Path(__file__).resolve().parents[1]
        / "src/scribeforge/runtime/locks/paddleocr-cpu-py312-universal.txt"
    ).read_text(encoding="utf-8")

    assert LINUX_HASH in lock  # protobuf manylinux2014_x86_64
    assert (
        "7ad270f438cbdd402c364980317fb6b117d9ec5e226fff5b4148dd9aa9fc6e02" in lock
    )  # numpy cp312 manylinux x86_64
    assert (
        "2c17f675a25ebd0675f703f7b82777ea92db0c360bd55a5c6b2dfaad7bc3f260" in lock
    )  # paddlepaddle cp312 manylinux1_x86_64
