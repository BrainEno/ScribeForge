"""Oracle engines: measure the pipeline's own text loss, not OCR accuracy.

A benchmark number is only meaningful next to the floor it can reach. This
module supplies engines that return the corpus ground truth verbatim, so running
the production alignment, risk, decision and assembly stages over them measures
*everything the pipeline itself can destroy* — furniture removal, line pairing,
adjudication, page-label stripping — with recognition error set to exactly zero.

It is explicitly **not** an OCR accuracy measurement and must never be reported
as one: the boxes are synthetic and the text is the answer key. Its purpose is to
turn "the pipeline loses text" from a suspicion into a number, and to prove the
end-to-end CER path works before a machine with model weights is available.

Real engines plug into the same two functions (``run_benchmark`` for raw engine
output, ``run_pipeline_benchmark`` for the fused result); nothing else changes.
"""

from __future__ import annotations

from pathlib import Path

from scribeforge.domain.ocr import BoundingBox, OCREngine, OCRLine, OCRToken, PageOCRResult


def _lines(texts: tuple[str, ...]) -> tuple[OCRLine, ...]:
    """Stack reference lines as evenly spaced full-width rows.

    Geometry only has to be *consistent*, never realistic: identical engines
    produce identical boxes, so alignment pairs line-for-line and the score
    isolates text handling.
    """

    count = max(len(texts), 1)
    height = 0.9 / count
    lines: list[OCRLine] = []
    for index, text in enumerate(texts):
        box = BoundingBox(0.05, 0.05 + index * height, 0.9, height * 0.6)
        lines.append(OCRLine(text, box, (OCRToken(text, 1.0, box),)))
    return tuple(lines)


class OracleEngine(OCREngine):
    """An engine that replays the corpus answer key for each page image."""

    def __init__(self, name: str = "oracle", *, version: str = "ground-truth") -> None:
        self._name = name
        self._version = version
        self._texts: dict[Path, tuple[str, ...]] = {}

    def register(self, image: Path, text: str) -> None:
        """Bind one page image to its reference transcription."""

        self._texts[image.resolve()] = tuple(text.splitlines())

    @property
    def name(self) -> str:
        return self._name

    def analyze_page(self, image_path: str, page_index: int) -> PageOCRResult:
        key = Path(image_path).resolve()
        if key not in self._texts:
            raise KeyError(f"oracle engine has no reference text for {image_path}")
        texts = self._texts[key]
        return PageOCRResult(page_index, self._name, self._version, _lines(texts))
