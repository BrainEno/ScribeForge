"""Analyze PDF characteristics relevant to OCR pipeline readiness.

This script extracts page-level metadata from the benchmark PDF to determine
whether the current ScribeForge pipeline is suited to the document.  It does
not require any OCR backend -- only pypdfium2 and Pillow, both already in the
project dependencies.
"""

from __future__ import annotations

import io
import json
import sys

import pypdfium2 as pdfium
from PIL import Image


def _image_stats(image: Image.Image) -> dict:
    """Compute per-channel statistics without numpy."""
    gray = image.convert("L")
    data = list(gray.tobytes())
    n = len(data)
    mean = sum(data) / n
    variance = sum((v - mean) ** 2 for v in data) / n
    std = variance ** 0.5
    dark = sum(1 for v in data if v < 128)
    min_v = min(data)
    max_v = max(data)
    gray.close()
    return {
        "mean_brightness": round(mean, 1),
        "std_brightness": round(std, 1),
        "dark_pixel_ratio": round(dark / n, 4),
        "min_brightness": min_v,
        "max_brightness": max_v,
    }


def full_analysis(pdf_path: str, *, sample_pages: list[int] | None = None, dpi: int = 300) -> dict:
    doc = pdfium.PdfDocument(pdf_path)
    total_pages = len(doc)
    metadata = doc.get_metadata_dict()

    if sample_pages is None:
        sample_pages = list(range(min(50, total_pages)))
    sample_pages = [p for p in sample_pages if 0 <= p < total_pages]

    pages: list[dict] = []
    for idx in sample_pages:
        page = doc[idx]
        _w_pts, _h_pts = page.get_size()
        bitmap = page.render(scale=dpi / 72.0)
        image = bitmap.to_pil()

        width_px, height_px = image.size
        stats = _image_stats(image)

        # Estimate PNG size
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png_kb = buf.tell() / 1024

        # Detect full-page image: high std but very low dark pixel ratio
        # suggests a colored/photographic page rather than text
        is_full_page_image = (
            stats["std_brightness"] > 30
            and stats["dark_pixel_ratio"] < 0.01
        )

        entry = {
            "page_index": idx,
            "width_px": width_px,
            "height_px": height_px,
            "mean_brightness": stats["mean_brightness"],
            "std_brightness": stats["std_brightness"],
            "dark_pixel_ratio": stats["dark_pixel_ratio"],
            "min_brightness": stats["min_brightness"],
            "max_brightness": stats["max_brightness"],
            "is_full_page_image": is_full_page_image,
            "estimated_png_kb": round(png_kb, 0),
        }
        image.close()
        page.close()

        pages.append(entry)

    doc.close()

    # Aggregates
    mean_b = sum(p["mean_brightness"] for p in pages) / len(pages)
    mean_dark = sum(p["dark_pixel_ratio"] for p in pages) / len(pages)
    avg_png_kb = sum(p["estimated_png_kb"] for p in pages) / len(pages)
    full_page_count = sum(1 for p in pages if p["is_full_page_image"])

    return {
        "pdf_path": pdf_path,
        "total_pages": total_pages,
        "metadata": metadata,
        "dpi": dpi,
        "sampled_pages": len(pages),
        "pages": pages,
        "aggregates": {
            "mean_brightness": round(mean_b, 1),
            "mean_dark_pixel_ratio": round(mean_dark, 4),
            "full_page_image_count_in_sample": full_page_count,
            "avg_page_png_kb": round(avg_png_kb, 0),
            "estimated_full_book_png_mb": round(avg_png_kb * total_pages / 1024, 1),
            "estimated_full_book_assets_mb": round(avg_png_kb * total_pages / 1024, 1),
        },
    }


def main() -> None:
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "benchmark/V..pdf"
    result = full_analysis(pdf_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
