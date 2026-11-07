#!/usr/bin/env python3
"""Compile the managed OCR environments into universal, hash-locked requirements."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "src" / "scribeforge" / "runtime" / "locks"
SOURCE_DIR = LOCK_DIR / "sources"
EXCLUDE_NEWER = "2026-11-07T00:00:00Z"
LOCK_NAMES = (
    "mineru-py312-universal.txt",
    "paddleocr-cpu-py312-universal.txt",
    "paddleocr-nvidia-cu118-py312-universal.txt",
    "paddleocr-nvidia-cu126-py312-universal.txt",
    "paddleocr-nvidia-cu129-py312-universal.txt",
    "surya-py312-universal.txt",
)
_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)")
_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


@cache
def _pypi_digests(name: str, version: str) -> frozenset[str]:
    """Return every official artifact digest for one immutable PyPI release."""

    request = urllib.request.Request(
        f"https://pypi.org/pypi/{name}/{version}/json",
        headers={"Accept": "application/json", "User-Agent": "ScribeForge-lock-compiler"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Paddle GPU distributions exist only on Paddle's custom index.
            return frozenset()
        raise
    digests = frozenset(
        artifact["digests"]["sha256"]
        for artifact in payload.get("urls", [])
        if re.fullmatch(r"[0-9a-f]{64}", artifact.get("digests", {}).get("sha256", ""))
    )
    if payload.get("urls") and not digests:
        raise RuntimeError(f"PyPI returned no SHA-256 artifact digests for {name}=={version}")
    return digests


def augment_pypi_hashes(
    body: str,
    digest_loader: Callable[[str, str], frozenset[str]] | None = None,
) -> str:
    """Merge PyPI's complete artifact hashes into uv's resolved lock.

    Paddle's simple index does not embed hashes. During universal compilation uv
    can therefore record only one downloaded artifact for a package, even when
    other supported platforms need different wheels. Existing custom-index
    hashes are retained; official PyPI hashes are additive and sorted.
    """

    if digest_loader is None:
        digest_loader = _pypi_digests
    lines = body.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        match = _REQUIREMENT.match(lines[index])
        if match is None:
            output.append(lines[index])
            index += 1
            continue

        block = [lines[index]]
        index += 1
        while index < len(lines) and _REQUIREMENT.match(lines[index]) is None:
            block.append(lines[index])
            index += 1
        hash_start = next(
            (i for i, line in enumerate(block) if line.lstrip().startswith("--hash=sha256:")),
            None,
        )
        if hash_start is None:
            raise RuntimeError(f"missing hashes for {match.group(1)}=={match.group(2)}")
        hash_end = hash_start
        while hash_end < len(block) and block[hash_end].lstrip().startswith("--hash=sha256:"):
            hash_end += 1
        existing = {digest for line in block[hash_start:hash_end] for digest in _HASH.findall(line)}
        official = digest_loader(match.group(1), match.group(2))
        if not official - existing:
            output.extend(block)
            continue
        combined = sorted(existing | official)
        continuation = " " + chr(92)
        hashes = [
            f"    --hash=sha256:{digest}{continuation if i + 1 < len(combined) else ''}"
            for i, digest in enumerate(combined)
        ]
        output.extend([block[0], *hashes, *block[hash_end:]])
    return "\n".join(output) + ("\n" if body.endswith("\n") else "")


def compile_lock(uv: str, name: str) -> str:
    source = SOURCE_DIR / name.replace(".txt", ".in")
    # Paddle's simple index omits upload timestamps, which uv rejects when a
    # global cutoff is present. The committed artifact hashes remain mandatory.
    date_arguments = (
        [] if name.startswith("paddleocr-") else ["--exclude-newer", EXCLUDE_NEWER]
    )
    with tempfile.TemporaryDirectory(prefix="scribeforge-lock-") as temporary:
        output = Path(temporary) / name
        subprocess.run(
            [
                uv,
                "pip",
                "compile",
                str(source.relative_to(ROOT)),
                "--universal",
                "--generate-hashes",
                "--python-version",
                "3.12",
                *date_arguments,
                "--index-strategy",
                "unsafe-first-match",
                "--no-header",
                "--output-file",
                str(output),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        body = augment_pypi_hashes(output.read_text(encoding="utf-8"))
    command = (
        "uv pip compile --universal --generate-hashes --python-version 3.12 "
        "--index-strategy unsafe-first-match "
    )
    if date_arguments:
        command += f"--exclude-newer {EXCLUDE_NEWER} "
    command += source.relative_to(ROOT).as_posix()
    return (
        "# Generated by scripts/compile_runtime_locks.py; do not edit by hand.\n"
        f"# {command}\n{body}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv", help="uv 0.13.7 or newer executable")
    parser.add_argument("--check", action="store_true", help="fail if committed locks differ")
    parser.add_argument("names", nargs="*", choices=LOCK_NAMES)
    args = parser.parse_args()
    names = tuple(args.names) or LOCK_NAMES
    stale = []
    for name in names:
        destination = LOCK_DIR / name
        expected = compile_lock(args.uv, name)
        if args.check:
            if not destination.is_file() or destination.read_text(encoding="utf-8") != expected:
                stale.append(name)
        else:
            destination.write_text(expected, encoding="utf-8")
            print(f"generated {destination.relative_to(ROOT)}")
    if stale:
        raise SystemExit("stale runtime locks: " + ", ".join(stale))


if __name__ == "__main__":
    main()
