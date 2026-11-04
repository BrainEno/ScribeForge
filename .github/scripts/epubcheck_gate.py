"""Generate a deterministic exported EPUB and require EPUBCheck positive/negative controls."""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import subprocess
import tempfile
import zipfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from scribeforge.adapters.epub import build_epub
from scribeforge.domain.content import (
    PageImageRole,
    VerifiedBook,
    VerifiedChapter,
    VerifiedImage,
    VerifiedPage,
)


def create_fixtures(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    Image.new("RGB", (16, 24), (220, 210, 190)).save(buffer, format="PNG")
    cover_data = buffer.getvalue()
    cover = VerifiedImage(
        0,
        "cover.png",
        "image/png",
        PageImageRole.COVER,
        hashlib.sha256(cover_data).hexdigest(),
        cover_data,
        alt="封面",
    )
    book = VerifiedBook(
        "ScribeForge EPUBCheck 测试",
        (
            VerifiedPage(0, ()),
            VerifiedPage(1, ("第一章", "古籍原文与标点。")),
            VerifiedPage(2, ("第二页的文字 & symbols.",)),
        ),
        chapters=(VerifiedChapter("第一章", 1, 1),),
        images=(cover,),
    )
    data = build_epub(
        book,
        identifier="urn:scribeforge:ci:fixture",
        language="zh",
        modified=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    valid = directory / "valid.epub"
    invalid = directory / "invalid.epub"
    valid.write_bytes(data)
    # Preserve the exported content but deliberately omit a required EPUB member.
    with zipfile.ZipFile(valid) as source, zipfile.ZipFile(invalid, "w") as output:
        for info in source.infolist():
            if info.filename != "mimetype":
                output.writestr(info, source.read(info.filename))
    print(f"Generated positive fixture: {valid}", flush=True)
    print(f"Generated negative fixture (missing mimetype): {invalid}", flush=True)
    return valid, invalid


def _run(command: Sequence[str], path: Path) -> subprocess.CompletedProcess[str]:
    print(f"Running EPUBCheck: {path}", flush=True)
    result = subprocess.run([*command, str(path)], text=True, capture_output=True, check=False)
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", flush=True)
    print(f"EPUBCheck exit code: {result.returncode}", flush=True)
    return result


def run_gate(command: Sequence[str], valid: Path, invalid: Path) -> None:
    result = _run(command, valid)
    if result.returncode != 0:
        raise RuntimeError("EPUBCheck rejected the valid EPUB fixture")
    result = _run(command, invalid)
    if result.returncode == 0:
        raise RuntimeError("EPUBCheck accepted the invalid EPUB fixture")
    if not re.search(r"\b(?:ERROR|FATAL)\([A-Z]+-\d+\)", result.stdout + result.stderr):
        raise RuntimeError("negative control failed without an official EPUBCheck error")
    print("EPUBCheck gate passed: valid accepted, invalid rejected.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jar", type=Path, help="path to the verified official EPUBCheck jar")
    args = parser.parse_args()
    if not args.jar.is_file():
        parser.error(f"EPUBCheck jar not found: {args.jar}")
    with tempfile.TemporaryDirectory(prefix="scribeforge-epubcheck-") as temp:
        valid, invalid = create_fixtures(Path(temp))
        run_gate(["java", "-jar", str(args.jar.resolve())], valid, invalid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
