from __future__ import annotations

import argparse
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _default_font_candidates() -> tuple[Path, ...]:
    if os.name == "nt":
        windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
        return (
            windows / "Fonts" / "arial.ttf",
            windows / "Fonts" / "calibri.ttf",
        )
    return (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    )


def create_smoke_image(output: Path, *, font_path: Path | None = None) -> Path:
    """Create the fixed synthetic input used for protocol-only OCR acceptance."""

    candidates = (font_path,) if font_path is not None else _default_font_candidates()
    selected = next((path for path in candidates if path.is_file()), None)
    if selected is None:
        choices = ", ".join(str(path) for path in candidates)
        raise FileNotFoundError(f"no usable font for Paddle smoke image: {choices}")

    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1600, 500), "white")
    draw = ImageDraw.Draw(image)
    font_large = ImageFont.truetype(str(selected), 64)
    font_small = ImageFont.truetype(str(selected), 52)
    draw.text((80, 90), "ScribeForge OCR 2026", fill="black", font=font_large)
    draw.text((80, 260), "Native word boxes 0123456789", fill="black", font=font_small)
    image.save(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path, default=None)
    args = parser.parse_args()
    output = create_smoke_image(args.output, font_path=args.font)
    print(f"synthetic smoke image: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
