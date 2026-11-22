"""Guard against exporting the prefix of an interrupted source import."""

from __future__ import annotations

from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.storage.sqlite import SQLiteStore


class IncompleteImportError(ValueError):
    """Import is known not to have finished; do not export a partial book."""


def require_completed_import(store: SQLiteStore, project_id: int) -> None:
    """Reject an explicitly unfinished import, and holes in completed imports.

    Historical/manual projects can lack an import job; their source extent is
    unknown, so this guard alone cannot prove that those projects are complete.
    """
    jobs = [job for job in store.list_jobs(project_id) if job.stage is JobStage.IMPORT]
    expected = store.source_page_count(project_id)
    if not jobs:
        if expected is not None:
            raise IncompleteImportError("source extent exists but the import job is missing")
        return
    job = jobs[0]
    if job.state is not JobState.SUCCEEDED:
        raise IncompleteImportError(
            f"source import is {job.state.value}; only imported pages are present. "
            "Resume processing with the original source before exporting."
        )
    indexes = [page.page_index for page in store.list_pages(project_id)]
    if expected is not None and len(indexes) != expected:
        raise IncompleteImportError(
            f"source has {expected} physical pages but project has {len(indexes)} imported pages; "
            "restore a verified backup or re-import the original source into a new project"
        )
    if any(index != position for position, index in enumerate(indexes)):
        raise IncompleteImportError(
            "completed import has missing physical page indexes; restore a verified backup "
            "or re-import the original source into a new project"
        )
