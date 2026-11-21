"""Exercise the installed backend's trusted EPUB export without model downloads.

Only OCR is substituted: the real process pipeline imports a scan and persists
conflicting OCR evidence. The installed executable must reject an unresolved
export, accept a human decision, export an EPUB and verify its receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

from PIL import Image, ImageDraw

from scribeforge.cli import Engines, run
from scribeforge.domain.ocr import BoundingBox, OCRLine, OCRToken, PageOCRResult

ACCEPTED = "繁體漢字"
OTHER = "另一候選"
SIMPLIFIED = "繁体汉字"


class FixtureEngine:
    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.text = text

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        with Image.open(image_path) as image:
            image.verify()
        box = BoundingBox(0.1, 0.1, 0.7, 0.1)
        token = OCRToken(self.text, 0.95, box)
        return PageOCRResult(page_index, self.name, "fixture", (OCRLine(self.text, box, (token,)),))


def _engines(_: Path) -> Engines:
    return Engines(
        primary=FixtureEngine("primary", ACCEPTED), secondary=FixtureEngine("secondary", OTHER)
    )


def _invoke(backend: Path, *arguments: str, expected: int = 0) -> str:
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    if os.name == "nt":
        env["PATH"] = str(Path(env["SystemRoot"]) / "System32")
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(backend), *arguments],
        env=env,
        cwd=backend.parent,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=35,
        check=False,
    )
    if result.returncode != expected:
        raise RuntimeError(
            f"{arguments[0]} exited {result.returncode}, expected {expected}: "
            f"{(result.stdout + result.stderr)[-4000:]}"
        )
    return result.stdout


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _check_epub(path: Path) -> None:
    with ZipFile(path) as archive:
        first = archive.infolist()[0]
        _require(
            first.filename == "mimetype" and first.compress_type == ZIP_STORED,
            "mimetype must be the first uncompressed EPUB entry",
        )
        _require(archive.read(first) == b"application/epub+zip", "bad EPUB media type")
        _require(archive.testzip() is None, "EPUB archive CRC failed")
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(".//{*}rootfile")
        _require(rootfile is not None, "missing EPUB container rootfile")
        assert rootfile is not None
        opf_path = Path(rootfile.attrib["full-path"])
        opf = ET.fromstring(archive.read(opf_path.as_posix()))
        _require(opf.attrib.get("version") == "3.0", "expected EPUB 3")
        items = {entry.attrib["id"]: entry for entry in opf.findall("./{*}manifest/{*}item")}
        nav = [entry for entry in items.values() if "nav" in entry.get("properties", "").split()]
        _require(len(nav) == 1, "expected one EPUB navigation document")
        ET.fromstring(archive.read((opf_path.parent / nav[0].attrib["href"]).as_posix()))
        texts = []
        for ref in opf.findall("./{*}spine/{*}itemref"):
            href = items[ref.attrib["idref"]].attrib["href"]
            chapter = ET.fromstring(archive.read((opf_path.parent / href).as_posix()))
            texts.append("".join(chapter.itertext()))
        text = "\n".join(texts)
        _require(SIMPLIFIED in text and OTHER not in text, "EPUB lost the accepted OCR decision")


def smoke(backend: Path, root: Path) -> None:
    source = root / "扫描 样本.png"
    image = Image.new("RGB", (160, 200), "white")
    ImageDraw.Draw(image).rectangle((16, 20, 120, 35), fill="black")
    image.save(source)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = root / "处理 记录"
    artifact = root / "导出 成品.epub"
    _require(
        run(["process", str(source), "--store", str(store)], engine_factory=_engines) == 0,
        "fixture OCR pipeline failed",
    )
    export = (
        "export",
        f"--store={store}",
        f"--out={artifact}",
        "--language=zh",
        "--normalization=simplified",
        "--strict-acceptance",
    )
    _invoke(backend, *export, expected=2)
    _require(not artifact.exists(), "unresolved OCR produced an EPUB")
    _invoke(backend, "resolve", f"--store={store}", "--page=0", "--pair=0", f"--text={ACCEPTED}")
    _invoke(backend, *export)
    _check_epub(artifact)
    result = json.loads(
        _invoke(backend, "verify-export", f"--file={artifact}", f"--source-sha256={source_digest}")
    )
    _require(
        result.get("status") == "consistent" and result.get("project_source_matches") is True,
        "saved EPUB or source receipt did not verify",
    )
    _require(hashlib.sha256(source.read_bytes()).hexdigest() == source_digest, "scan was modified")
    print(
        "Installed backend: unresolved export refused, accepted OCR exported and EPUB receipt verified."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="scribeforge-epub-") as directory:
        smoke(args.backend.resolve(strict=True), Path(directory))


if __name__ == "__main__":
    main()
