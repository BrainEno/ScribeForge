"""Read-only explanation of the same content gate used by trusted export."""

from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from scribeforge.domain.content import VerifiedBook
from scribeforge.pipeline.assembly import (
    PageImageError,
    UnresolvedContentError,
    assemble_verified_content,
)
from scribeforge.pipeline.layout_evidence import collect_layout_observations
from scribeforge.storage.sqlite import SQLiteStore

_MAX_INDEX = (1 << 63) - 1
_MAX_ISSUES = 400
IssueKind = Literal[
    "unverified_page",
    "unresolved_pair",
    "unconfirmed_page",
    "spot_check",
    "page_image",
    "evidence_error",
    "no_pages",
    "layout_region",
]


@dataclass(frozen=True, slots=True)
class ReadinessOptions:
    include_page_furniture: bool = False
    strict_acceptance: bool = False
    page: int | None = None
    limit: int = 120

    def __post_init__(self) -> None:
        if self.page is not None and not 0 <= self.page <= _MAX_INDEX:
            raise ValueError("invalid physical page index")
        if not 1 <= self.limit <= _MAX_ISSUES:
            raise ValueError(f"readiness issue limit must be between 1 and {_MAX_ISSUES}")


@dataclass(frozen=True, slots=True)
class ReadinessIssue:
    kind: IssueKind
    blocking: bool = True
    page: int | None = None
    pair: int | None = None
    detail: str = ""
    reasons: tuple[str, ...] = ()
    characters: tuple[str, ...] = ()
    actor: str | None = None
    score: float | None = None
    ink: float | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "blocking": self.blocking,
            "page_index": self.page,
            "pair_index": self.pair,
            "detail": self.detail[:1600],
            "reasons": list(self.reasons[:16]),
            "characters": list(self.characters[:16]),
            "actor": self.actor,
            "score": self.score,
            "ink": self.ink,
        }


@dataclass(slots=True)
class _Findings:
    options: ReadinessOptions
    total: Counter[IssueKind] = field(default_factory=Counter)
    blocking: int = 0
    visible_count: int = 0
    visible: list[ReadinessIssue] = field(default_factory=list)

    def add(self, issue: ReadinessIssue) -> None:
        self.total[issue.kind] += 1
        self.blocking += issue.blocking
        if (
            self.options.page is not None
            and issue.page != self.options.page
            and issue.page is not None
        ):
            return
        self.visible_count += 1
        if len(self.visible) < self.options.limit:
            self.visible.append(issue)

    def unresolved(self, error: UnresolvedContentError) -> None:
        # Deterministic physical order makes the bounded preview useful.
        rows = [ReadinessIssue("unverified_page", page=page) for page in error.unverified_pages]
        rows.extend(
            ReadinessIssue("unconfirmed_page", page=page, ink=error.measured_ink.get(page))
            for page in error.unconfirmed_pages
        )
        rows.extend(
            ReadinessIssue(
                "unresolved_pair",
                page=entry.page_index,
                pair=entry.pair_index,
                reasons=entry.blockers,
                characters=entry.characters,
            )
            for entry in error.unresolved_pairs
        )
        for issue in sorted(
            rows, key=lambda item: (item.page or 0, item.pair if item.pair is not None else -1)
        ):
            self.add(issue)

    def acceptance(self, book: VerifiedBook) -> None:
        if not book.pages:
            self.add(ReadinessIssue("no_pages"))
        for check in book.acceptance.spot_check:
            self.add(
                ReadinessIssue(
                    "spot_check",
                    blocking=strict_acceptance_blocks(book, self.options.strict_acceptance),
                    page=check.page_index,
                    pair=check.pair_index,
                    detail=check.note,
                    reasons=tuple(reason.value for reason in check.reasons),
                    actor=check.actor,
                    score=check.score,
                )
            )


def strict_acceptance_blocks(book: VerifiedBook, strict: bool) -> bool:
    """One predicate shared by readiness and the actual export handler."""
    return strict and book.acceptance.requires_human_review


def _summarize_book(book: VerifiedBook) -> dict[str, object]:
    acceptance = book.acceptance
    return {
        "pages": len(book.pages),
        "chapters": len(book.chapters),
        "images": len(book.images),
        "confirmed_textless_pages": len(book.confirmed_blank_pages),
        "omitted_furniture_pairs": len(book.dropped_furniture),
        "accepted_pairs": acceptance.pairs,
        "machine_accepted": acceptance.machine_accepted,
        "human_accepted": acceptance.human_accepted,
        "max_machine_risk": acceptance.max_machine_risk,
    }


def read_export_readiness(database: Path, options: ReadinessOptions) -> dict[str, object]:
    """Assess current content without initializing the store or writing exports.

    Assembly uses several connections. A separate connection's data_version
    detects commits during the read rather than presenting a mixed result as a
    valid assessment. The actual export must still run its own gate.
    """
    store = SQLiteStore(database, read_only=True)
    findings = _Findings(options)
    summary = None
    uri = f"{database.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as observer:
        before = observer.execute("PRAGMA data_version").fetchone()[0]
        projects = store.list_projects()
        if not projects:
            raise ValueError("no projects in store")
        # Match the current trusted export behavior for multi-project stores.
        project = projects[0]
        if options.page is not None and not any(
            page.page_index == options.page for page in store.list_pages(project.id)
        ):
            raise ValueError("physical page was not found in the export project")
        try:
            book = assemble_verified_content(
                store, project.id, include_page_furniture=options.include_page_furniture
            )
        except UnresolvedContentError as exc:
            findings.unresolved(exc)
        except PageImageError as exc:
            findings.add(ReadinessIssue("page_image", page=exc.page_index, detail=str(exc)))
        except (ValueError, KeyError) as exc:
            findings.add(ReadinessIssue("evidence_error", detail=str(exc)))
        else:
            findings.acceptance(book)
            summary = _summarize_book(book)
        embedded = frozenset(image.page_index for image in book.images) if summary else frozenset()
        try:
            layout = collect_layout_observations(store, project.id, embedded_pages=embedded)
        except (ValueError, KeyError) as exc:
            findings.add(ReadinessIssue("evidence_error", detail=str(exc)))
            layout = ()
        for observation in layout:
            if observation.may_be_omitted:
                findings.add(
                    ReadinessIssue(
                        "layout_region",
                        blocking=False,
                        page=observation.page_index,
                        detail=observation.warning(),
                    )
                )
        after = observer.execute("PRAGMA data_version").fetchone()[0]
        if before != after:
            raise ValueError("project changed during export readiness check; run it again")
    return {
        "schema_version": 1,
        "project_id": project.id,
        "project_title": project.title,
        "options": {
            "include_page_furniture": options.include_page_furniture,
            "strict_acceptance": options.strict_acceptance,
            "page_index": options.page,
        },
        "ready": findings.blocking == 0,
        "blocking_count": findings.blocking,
        "issue_counts": dict(findings.total),
        "matching_count": findings.visible_count,
        "issues": [issue.as_json() for issue in findings.visible],
        "summary": summary,
    }
