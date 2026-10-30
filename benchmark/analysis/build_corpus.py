"""Build a benchmark corpus directory from a real scan PDF.

The corpus rules in ``docs/benchmark-corpus.md`` require every page to come from
a real scan, with a hand transcription and clear rights. This script does the
mechanical half — rasterise the chosen physical pages, write the manifest, and
create transcription placeholders — so adding a corpus is one command instead of
an error-prone manual ritual. It never invents text: placeholders are empty and
``validate_corpus()`` refuses to score a corpus until a human fills them in.

Usage::

    python benchmark/analysis/build_corpus.py \
        --pdf /path/to/public-domain-scan.pdf \
        --out benchmark/corpus/ancient \
        --name ancient \
        --pages 3,7,11 \
        --direction vertical-rl \
        --language zh-Hant \
        --dpi 200 \
        --source "《四部叢刊》影印本，公有领域"

The page numbers are physical PDF indexes (0-based), matching ``benchmark/corpus/v``.
"""

from __future__ import annotations

import argparse
import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from scribeforge.adapters.pdf_pages import PdfPageSource
from scribeforge.benchmark.corpus import validate_corpus
from scribeforge.benchmark.manifest import load_manifest

DIRECTIONS = ("horizontal-lr", "horizontal-rl", "vertical-rl", "vertical-lr")


def _page_id(name: str, page_index: int) -> str:
    return f"{name}-{page_index:03d}"


def _write_page_image(pdf: Path, page_index: int, dpi: int, destination: Path) -> None:
    """Render one physical page as grayscale PNG.

    Grayscale is deliberate: colour scans of old paper spend most of their bytes on
    a paper tint that carries no text information, and the corpus has to stay small
    enough to ship with the repository.
    """

    source = PdfPageSource(pdf, dpi=dpi)
    if page_index >= source.page_count:
        raise SystemExit(
            f"error: page {page_index} is outside the PDF (0..{source.page_count - 1})"
        )
    rendered = source.render_page(page_index)
    with Image.open(BytesIO(rendered.image_bytes)) as raster:
        image = raster.convert("L")
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="PNG", optimize=True)


def build_corpus(
    *,
    pdf: Path,
    out: Path,
    name: str,
    pages: tuple[int, ...],
    direction: str | None,
    language: str,
    dpi: int,
    source_note: str,
    tags: tuple[str, ...] = ("body",),
) -> dict[str, object]:
    """Create images, manifest and empty transcription files; return the manifest."""

    if direction is not None and direction not in DIRECTIONS:
        raise ValueError(f"unknown direction: {direction}")
    if not pages:
        raise ValueError("at least one page index is required")
    if len(set(pages)) != len(pages):
        raise ValueError("page indexes must be unique")

    cases: list[dict[str, object]] = []
    for page_index in pages:
        case_id = _page_id(name, page_index)
        image_path = Path("images") / f"page_{page_index:03d}.png"
        truth_path = Path("ground_truth") / f"{case_id}.txt"
        _write_page_image(pdf, page_index, dpi, out / image_path)
        truth = out / truth_path
        truth.parent.mkdir(parents=True, exist_ok=True)
        if not truth.exists():
            # Empty on purpose: an empty transcription must block scoring rather
            # than let a page score a perfect zero against itself.
            truth.write_text("", encoding="utf-8")
        case: dict[str, object] = {
            "id": case_id,
            "image": image_path.as_posix(),
            "ground_truth": truth_path.as_posix(),
            "language": language,
            "tags": list(tags),
        }
        if direction is not None:
            case["direction"] = direction
        cases.append(case)

    manifest: dict[str, object] = {
        "schema_version": 1,
        "description": source_note,
        "source": {"pdf": str(pdf), "dpi": dpi},
        "ground_truth_review_status": "unreviewed",
        "ground_truth_note": (
            "Transcriptions have not yet been added. After filling ground_truth/*.txt, "
            "set ground_truth_review_status to single-pass or double-proofread to match "
            "the work performed. Empty files are rejected by validate_corpus()."
        ),
        "cases": cases,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", required=True, help="Corpus prefix for case ids.")
    parser.add_argument("--pages", required=True, help="Comma-separated physical page indexes.")
    parser.add_argument("--direction", default=None, choices=DIRECTIONS)
    parser.add_argument("--language", default="zh-Hant")
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--tags", default="body", help="Comma-separated tags.")
    parser.add_argument("--source", default="", help="Provenance and rights note.")
    args = parser.parse_args()

    pages = tuple(int(value) for value in args.pages.split(",") if value.strip())
    build_corpus(
        pdf=args.pdf,
        out=args.out,
        name=args.name,
        pages=pages,
        direction=args.direction,
        language=args.language,
        dpi=args.dpi,
        source_note=args.source,
        tags=tuple(tag for tag in args.tags.split(",") if tag.strip()),
    )

    manifest = load_manifest(args.out / "manifest.json")
    problems = validate_corpus(manifest, args.out)
    if problems:
        print("corpus written; transcription still required:")
        for problem in problems:
            print(f"  - case {problem.case_id}: {problem.problem}")
        return 0
    print(f"corpus written and valid: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
