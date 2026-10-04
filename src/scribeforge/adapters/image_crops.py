from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PIL import Image

from scribeforge.domain.crops import ReviewCropPlan


def _validate_plans(plans: Sequence[ReviewCropPlan]) -> None:
    page_index = plans[0].page_index
    pair_indexes: set[int] = set()
    file_names: set[str] = set()
    for plan in plans:
        if plan.page_index != page_index:
            raise ValueError("review crop plans must describe the same page")
        if plan.pair_index in pair_indexes:
            raise ValueError(f"duplicate pair index in crop plans: {plan.pair_index}")
        if Path(plan.file_name).name != plan.file_name:
            raise ValueError("review crop file name must not contain path components")
        if plan.file_name in file_names:
            raise ValueError(f"duplicate review crop file name: {plan.file_name}")
        pair_indexes.add(plan.pair_index)
        file_names.add(plan.file_name)


def materialize_review_crops(
    source_image: Path,
    plans: Sequence[ReviewCropPlan],
    output_dir: Path,
) -> dict[int, str]:
    if not plans:
        return {}

    _validate_plans(plans)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[int, str] = {}
    with Image.open(source_image) as page:
        page_width, page_height = page.size
        for plan in plans:
            box = plan.box
            right = box.x + box.width
            bottom = box.y + box.height
            if right > page_width or bottom > page_height:
                raise ValueError(
                    f"review crop for pair {plan.pair_index} exceeds actual page bounds"
                )

            destination = output_dir / plan.file_name
            temporary = output_dir / f".{plan.file_name}.tmp"
            crop = page.crop((box.x, box.y, right, bottom))
            try:
                crop.save(temporary, format="PNG", optimize=False, compress_level=6)
                temporary.replace(destination)
            finally:
                crop.close()
                temporary.unlink(missing_ok=True)
            paths[plan.pair_index] = str(destination)

    return paths
