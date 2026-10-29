from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.domain.page_asset import PageAssetEvidence, reencode_image
from scribeforge.domain.source import PageSource, page_asset_name
from scribeforge.storage.sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class ImportResult:
    project_id: int
    job_id: int
    imported_page_indexes: tuple[int, ...]
    page_count: int


def _write_asset(destination: Path, data: bytes) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def import_source(
    store: SQLiteStore,
    project_id: int,
    source: PageSource,
    assets_dir: Path,
    *,
    reencode: dict[str, object] | None = None,
) -> ImportResult:
    """Materialize every page of a source into project-owned assets, resumably.

    Pages already persisted for the project are skipped so an interrupted import
    can be resumed without rewriting existing source evidence. The project-scoped
    import job records failure details and can be retried.

    When ``reencode`` is given (for example ``{"format": "jpeg", "quality": 85}``)
    every rendered page is re-encoded before it is written to disk. The re-encode
    parameters are stored with the page's asset evidence so the audit can tell a
    re-encoded raster from the original render, and the page's digest is computed
    from the re-encoded bytes — the audit always compares against what is actually
    on disk.
    """

    page_count = source.page_count
    if page_count < 1:
        raise ValueError("page source must contain at least one page")

    job_id = store.ensure_job(project_id, JobStage.IMPORT)
    if store.load_job(job_id).state is JobState.SUCCEEDED:
        return ImportResult(project_id, job_id, (), page_count)

    store.start_job(job_id)
    assets_dir.mkdir(parents=True, exist_ok=True)
    already_imported = store.imported_page_indexes(project_id)
    imported: list[int] = []
    try:
        for page_index in range(page_count):
            if page_index in already_imported:
                continue
            rendered = source.render_page(page_index)
            if rendered.page_index != page_index:
                raise ValueError("rendered page identity does not match requested page")

            data = rendered.image_bytes
            extension = rendered.extension
            render_parameters = dict(rendered.render_parameters)
            if reencode is not None:
                fmt = str(reencode.get("format", "jpeg"))
                quality_raw = reencode.get("quality")
                quality = None if quality_raw is None else cast(int, quality_raw)
                data, extension = reencode_image(
                    data, format=fmt, quality=quality
                )
                render_parameters["reencoded"] = {
                    "format": fmt,
                    "quality": quality,
                }

            destination = assets_dir / page_asset_name(page_index, extension)
            _write_asset(destination, data)
            page_asset = PageAssetEvidence.from_bytes(
                data,
                render_parameters=render_parameters,
            )
            store.add_page(
                project_id,
                page_index,
                str(destination),
                rendered.width,
                rendered.height,
                page_asset=page_asset,
            )
            imported.append(page_index)
    except Exception as exc:
        store.fail_job(job_id, str(exc).strip() or type(exc).__name__)
        raise
    store.complete_job(job_id)
    return ImportResult(project_id, job_id, tuple(imported), page_count)
