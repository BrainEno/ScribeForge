"""Presentation logic for the desktop shell.

This module never imports a GUI framework. It turns persisted evidence into the
rows and labels a view renders, so the desktop shell stays a display layer over
the same `SQLiteStore` and adjudication API the CLI uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scribeforge.domain.jobs import JobState
from scribeforge.pipeline.adjudication import (
    ReviewDetail,
    record_human_decision,
    review_detail,
    review_queue,
)
from scribeforge.storage.sqlite import SQLiteStore


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    """One project as the library list shows it."""

    project_id: int
    title: str
    source_uri: str
    page_count: int
    review_queued: int
    failed_jobs: int

    @property
    def summary(self) -> str:
        return (
            f"{self.title} — {self.page_count} page(s), "
            f"{self.review_queued} queued for review, {self.failed_jobs} failed job(s)"
        )


@dataclass(frozen=True, slots=True)
class JobRow:
    """One persisted job as the progress list shows it."""

    stage: str
    scope: str
    state: str
    attempts: int
    last_error: str | None

    @property
    def label(self) -> str:
        label = f"{self.stage} [{self.scope}] {self.state} attempts={self.attempts}"
        return label if self.last_error is None else f"{label} error={self.last_error}"


@dataclass(frozen=True, slots=True)
class ReviewRow:
    """One queued pair as the review list shows it."""

    page_index: int
    pair_index: int
    score: float
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        reasons = ",".join(self.reasons) if self.reasons else "none"
        return (
            f"page {self.page_index} pair {self.pair_index} "
            f"score={self.score:.2f} reasons={reasons}"
        )


def load_library(store: SQLiteStore) -> tuple[LibraryEntry, ...]:
    """Summarize every project in the store."""

    entries: list[LibraryEntry] = []
    for project in store.list_projects():
        entries.append(
            LibraryEntry(
                project_id=project.id,
                title=project.title,
                source_uri=project.source_uri,
                page_count=len(store.list_pages(project.id)),
                review_queued=len(review_queue(store, project.id)),
                failed_jobs=sum(
                    1
                    for job in store.list_jobs(project.id)
                    if job.state is JobState.FAILED
                ),
            )
        )
    return tuple(entries)


def load_jobs(store: SQLiteStore, project_id: int) -> tuple[JobRow, ...]:
    """List a project's jobs in the order they were created."""

    return tuple(
        JobRow(
            stage=job.stage.value,
            scope="project" if job.page_index is None else f"page {job.page_index}",
            state=job.state.value,
            attempts=job.attempts,
            last_error=job.last_error,
        )
        for job in store.list_jobs(project_id)
    )


def load_review_rows(store: SQLiteStore, project_id: int) -> tuple[ReviewRow, ...]:
    """List the pairs that still need a human decision."""

    return tuple(
        ReviewRow(
            page_index=item.page_index,
            pair_index=item.pair_index,
            score=item.score,
            reasons=tuple(reason.value for reason in item.reasons),
        )
        for item in review_queue(store, project_id)
    )


def resolve_review_row(
    store: SQLiteStore,
    project_id: int,
    row: ReviewRow,
    selected_text: str,
) -> int:
    """Record a human decision for a pair that is still awaiting review.

    The decision is append-only and cites the newest persisted evidence. A stale
    queue row is rejected rather than silently creating a second decision.
    The selected text is stored literally, including whitespace.
    """

    if not any(
        item.page_index == row.page_index and item.pair_index == row.pair_index
        for item in load_review_rows(store, project_id)
    ):
        raise ValueError("review item is no longer queued")
    return record_human_decision(
        store, project_id, row.page_index, row.pair_index, selected_text
    )


def load_detail(
    store: SQLiteStore,
    project_id: int,
    row: ReviewRow,
    *,
    crops_dir: Path | None = None,
) -> ReviewDetail:
    """Load the competing evidence for one queued pair."""

    return review_detail(
        store, project_id, row.page_index, row.pair_index, crops_dir=crops_dir
    )


@dataclass(frozen=True, slots=True)
class PairView:
    """Everything the shell shows for one queued pair: evidence and imagery."""

    detail: ReviewDetail
    page_image: Path | None
    crop_image: Path | None

    @property
    def sources_label(self) -> str:
        page = "page image" if self.page_image is not None else "no page image"
        crop = "crop" if self.crop_image is not None else "no crop"
        return f"{page}, {crop}"


def page_image_path(
    store: SQLiteStore, project_id: int, page_index: int
) -> Path | None:
    """Absolute path of a page's immutable raster asset, when it still exists."""

    for page in store.list_pages(project_id):
        if page.page_index == page_index:
            path = Path(page.source_image)
            return path if path.is_file() else None
    return None


def load_pair_view(
    store: SQLiteStore,
    project_id: int,
    row: ReviewRow,
    *,
    crops_dir: Path | None = None,
) -> PairView:
    """Load the evidence and the available imagery for one queued pair."""

    detail = load_detail(store, project_id, row, crops_dir=crops_dir)
    return PairView(
        detail=detail,
        page_image=page_image_path(store, project_id, row.page_index),
        crop_image=None if detail.crop_path is None else Path(detail.crop_path),
    )


def render_detail(detail: ReviewDetail) -> str:
    """Render the evidence panel text for one pair."""

    reasons = ",".join(reason.value for reason in detail.reasons) or "none"
    return "\n".join(
        (
            f"page {detail.page_index} pair {detail.pair_index}",
            f"risk score={detail.score:.2f} reasons={reasons}",
            f"primary {_render(detail.primary_text)}",
            f"secondary {_render(detail.secondary_text)}",
            f"vlm {_render_vlm(detail)}",
            f"crop {detail.crop_path or 'none'}",
            f"accepted {_render(detail.accepted_text)}",
        )
    )


def _render(text: str | None) -> str:
    return "none" if text is None else repr(text)


def _render_vlm(detail: ReviewDetail) -> str:
    if detail.vlm_text is None:
        return "none"
    uncertain = "true" if detail.vlm_uncertain else "false"
    return f"{detail.vlm_text!r} uncertain={uncertain}"
