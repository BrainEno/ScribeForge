from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from scribeforge.domain.crops import ReviewCropPlan


class PillowCropWriter:
    """Write deterministic review-candidate PNG crops from one decoded page."""

    def write(
        self,
        source_image: Path,
        output_dir: Path,
        plans: Sequence[ReviewCropPlan],
    ) -> dict[int, str]:
        self._validate_identity(plans)
        output_dir.mkdir(parents=True, exist_ok=True)
        results: dict[int, str] = {}

        with Image.open(source_image) as source:
            source.load()
            for plan in plans:
                self._validate_bounds(plan, source.width, source.height)
                destination = output_dir / plan.file_name
                crop = source.crop(
                    (
                        plan.box.x,
                        plan.box.y,
                        plan.box.x + plan.box.width,
                        plan.box.y + plan.box.height,
                    )
                )
                self._atomic_png(crop, destination)
                results[plan.pair_index] = str(destination)
        return results

    @staticmethod
    def _validate_identity(plans: Sequence[ReviewCropPlan]) -> None:
        pairs: set[int] = set()
        names: set[str] = set()
        for plan in plans:
            if plan.pair_index in pairs:
                raise ValueError(f"duplicate review pair index: {plan.pair_index}")
            if plan.file_name in names:
                raise ValueError(f"duplicate review crop file name: {plan.file_name}")
            if Path(plan.file_name).name != plan.file_name:
                raise ValueError("review crop file name must not contain a path")
            pairs.add(plan.pair_index)
            names.add(plan.file_name)

    @staticmethod
    def _validate_bounds(plan: ReviewCropPlan, width: int, height: int) -> None:
        right = plan.box.x + plan.box.width
        bottom = plan.box.y + plan.box.height
        if right > width or bottom > height:
            raise ValueError(
                f"review crop for pair {plan.pair_index} is outside source image bounds"
            )

    @staticmethod
    def _atomic_png(image: Image.Image, destination: Path) -> None:
        handle = tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        )
        temporary = Path(handle.name)
        handle.close()
        try:
            image.save(temporary, format="PNG")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
