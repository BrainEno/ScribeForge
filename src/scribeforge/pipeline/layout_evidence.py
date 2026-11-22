"""Read-only inventory of OCR-reported non-text regions.

These are model observations, not human-confirmed illustrations. In particular,
no coordinate is transferred from the OCR frame to the original page, no two
models' boxes are merged, and absence of a report is not proof of absence.
"""

from __future__ import annotations

from dataclasses import dataclass

from scribeforge.domain.ocr import BlockType, BoundingBox
from scribeforge.storage.sqlite import OCRInputFrame, SQLiteStore


@dataclass(frozen=True, slots=True)
class RegionObservation:
    page_index: int
    verification_id: int
    run_id: int
    engine: str
    block_index: int
    block_type: BlockType
    box: BoundingBox
    frame: OCRInputFrame | None

    def as_json(self) -> dict[str, object]:
        frame = self.frame
        return {
            "page_index": self.page_index,
            "verification_id": self.verification_id,
            "run_id": self.run_id,
            "engine": self.engine,
            "block_index": self.block_index,
            "type": self.block_type.value,
            "box": {
                "x": self.box.x,
                "y": self.box.y,
                "width": self.box.width,
                "height": self.box.height,
            },
            "frame": (
                None
                if frame is None
                else {
                    "kind": frame.kind,
                    "sha256": frame.sha256,
                    "width": frame.width,
                    "height": frame.height,
                }
            ),
        }


@dataclass(frozen=True, slots=True)
class PageLayoutObservation:
    page_index: int
    regions: tuple[RegionObservation, ...]
    embedded_page_image: bool

    @property
    def may_be_omitted(self) -> bool:
        """Whole-page EPUB images preserve pixels, not necessarily semantics."""
        return not self.embedded_page_image

    def as_json(self) -> dict[str, object]:
        return {
            "page_index": self.page_index,
            "embedded_page_image": self.embedded_page_image,
            "may_be_omitted": self.may_be_omitted,
            "regions": [region.as_json() for region in self.regions],
        }

    def warning(self) -> str:
        counts: dict[str, int] = {}
        for region in self.regions:
            label = f"{region.engine}/{region.block_type.value}"
            counts[label] = counts.get(label, 0) + 1
        description = ", ".join(f"{label}: {count}" for label, count in sorted(counts.items()))
        unknown = sum(region.frame is None for region in self.regions)
        frame_note = f"; {unknown} observation(s) have unknown OCR frame" if unknown else ""
        return (
            f"OCR reported possible figure/table regions ({description}){frame_note}; "
            "mixed-page regions are not embedded by the current exporter. "
            "Inspect the source page; model boxes are not human confirmation."
        )


def collect_layout_observations(
    store: SQLiteStore,
    project_id: int,
    *,
    embedded_pages: frozenset[int] = frozenset(),
) -> tuple[PageLayoutObservation, ...]:
    """Inventory only the two runs bound to each page's latest verification.

    Scope is deliberate: old or crop-based re-reads cannot establish current
    page layout. A missing verification produces no observation, not a clean bill
    of health. Callers must make the limitation visible independently.
    """
    pages: list[PageLayoutObservation] = []
    for page in store.list_pages(project_id):
        try:
            verification_id = store.latest_verification_id(project_id, page.page_index)
        except KeyError:
            continue
        verification = store.load_verification(verification_id)
        regions: list[RegionObservation] = []
        for run_id in (verification.primary_run_id, verification.secondary_run_id):
            result = store.load_ocr_run(run_id)
            frame = store.load_ocr_frame(run_id)
            for block_index, block in enumerate(result.blocks):
                if block.block_type not in (BlockType.FIGURE, BlockType.TABLE):
                    continue
                regions.append(
                    RegionObservation(
                        page.page_index,
                        verification_id,
                        run_id,
                        result.engine,
                        block_index,
                        block.block_type,
                        block.box,
                        frame,
                    )
                )
        if regions:
            pages.append(
                PageLayoutObservation(
                    page.page_index, tuple(regions), page.page_index in embedded_pages
                )
            )
    return tuple(pages)
