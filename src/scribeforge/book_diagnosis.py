"""Read-only comparison of source, project, and ScribeForge EPUB page evidence."""

from __future__ import annotations

import re
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path
from typing import cast
from xml.etree import ElementTree

from scribeforge.adapters.epub_builder import UNVERIFIED_CANDIDATE_WARNING
from scribeforge.adapters.sources import open_page_source
from scribeforge.domain.jobs import JobStage, JobState
from scribeforge.pipeline.source_snapshot import verified_source_snapshot
from scribeforge.storage.sqlite import SQLiteStore

_OPF = "{http://www.idpf.org/2007/opf}"
_EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
_MAX_ENTRY = 16 * 1024 * 1024
_MAX_TOTAL = 128 * 1024 * 1024


def _epub_page_evidence(path: Path) -> dict[str, object]:
    """Count source markers in ScribeForge's spine, not a reader's screen pages.

    Other publishers' page markers need not use these IDs; do not infer that
    their absence means missing content. Never extract paths from the archive.
    """
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ValueError("EPUB contains duplicate archive entries")

        def read(name: str) -> bytes:
            info = archive.getinfo(name)
            if info.file_size > _MAX_ENTRY:
                raise ValueError("EPUB XML entry exceeds inspection limit")
            return archive.read(info)

        package = ElementTree.fromstring(read("OEBPS/content.opf"))
        candidate_book = UNVERIFIED_CANDIDATE_WARNING in " ".join(package.itertext())
        manifest = package.find(f"{_OPF}manifest")
        spine = package.find(f"{_OPF}spine")
        if manifest is None or spine is None:
            raise ValueError("EPUB lacks manifest or spine")
        hrefs = {item.get("id"): item.get("href") for item in manifest.findall(f"{_OPF}item")}
        refs = spine.findall(f"{_OPF}itemref")
        if not refs:
            raise ValueError("EPUB spine is empty")
        indexes: list[int] = []
        styles: set[str] = set()
        total = 0
        for ref in refs:
            href = hrefs.get(ref.get("idref"))
            if href is None or not re.fullmatch(r"[A-Za-z0-9_.-]+\.xhtml", href):
                raise ValueError("unsupported EPUB spine path for ScribeForge inspection")
            name = f"OEBPS/{href}"
            info = archive.getinfo(name)
            total += info.file_size
            if total > _MAX_TOTAL:
                raise ValueError("EPUB spine exceeds inspection limit")
            root = ElementTree.fromstring(read(name))
            markers = []
            for node in root.iter():
                if "pagebreak" in (node.get(_EPUB_TYPE) or "").split():
                    marker = re.fullmatch(r"page-(\d+)", node.get("id") or "")
                    if marker:
                        markers.append(int(marker.group(1)))
            candidate = re.fullmatch(r"page-(\d{6})\.xhtml", href)
            if markers:
                styles.add("pagebreak_markers")
                indexes.extend(markers)
            elif candidate and candidate_book and int(candidate.group(1)) > 0:
                styles.add("candidate_page_documents")
                indexes.append(int(candidate.group(1)) - 1)
            else:
                styles.add("unrecognized_document")
        unique = set(indexes)
        return {
            "spine_documents": len(refs),
            "evidence_styles": sorted(styles),
            "source_page_markers": len(unique),
            "duplicate_markers": len(indexes) - len(unique),
            "page_indexes": sorted(unique),
        }


def diagnose_book(database: Path, source: Path, epub: Path) -> dict[str, object]:
    if not database.is_file():
        raise ValueError("project store not found")
    store = SQLiteStore(database, read_only=True)
    with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as observer:
        before = observer.execute("PRAGMA data_version").fetchone()[0]
        projects = store.list_projects()
        if len(projects) != 1:
            raise ValueError("diagnosis requires a single-project store")
        project = projects[0]
        with verified_source_snapshot(source, project.source_sha256) as snapshot:
            source_count = open_page_source(snapshot).page_count
        pages = store.list_pages(project.id)
        indexes = {page.page_index for page in pages}
        jobs = store.list_jobs(project.id)
        import_job = next((job for job in jobs if job.stage is JobStage.IMPORT), None)
        primary = {
            job.page_index
            for job in jobs
            if job.stage is JobStage.PRIMARY_OCR and job.state is JobState.SUCCEEDED
        }
        secondary = {
            job.page_index
            for job in jobs
            if job.stage is JobStage.SECONDARY_OCR and job.state is JobState.SUCCEEDED
        }
        verified = []
        for page in pages:
            try:
                store.latest_verification_id(project.id, page.page_index)
            except KeyError:
                continue
            verified.append(page.page_index)
        epub_info = _epub_page_evidence(epub)
        epub_indexes = set(cast(list[int], epub_info.pop("page_indexes")))
        if before != observer.execute("PRAGMA data_version").fetchone()[0]:
            raise ValueError("project changed during diagnosis; run it again")
    expected = set(range(source_count))
    return {
        "schema_version": 1,
        "source_sha256_matches_project": True,
        "source_physical_pages": source_count,
        "imported_pages": len(indexes),
        "missing_imported_count": len(expected - indexes),
        "missing_imported_pages_1based": [i + 1 for i in sorted(expected - indexes)][:100],
        "unexpected_imported_pages_1based": [i + 1 for i in sorted(indexes - expected)][:100],
        "import_state": None if import_job is None else import_job.state.value,
        "both_ocr_succeeded_pages": len(primary & secondary & expected),
        "verified_pages": len(verified),
        "failed_jobs": sum(job.state is JobState.FAILED for job in jobs),
        "running_jobs": sum(job.state is JobState.RUNNING for job in jobs),
        "epub": {
            **epub_info,
            "missing_source_page_marker_count": len(expected - epub_indexes),
            "missing_source_page_markers_1based": [i + 1 for i in sorted(expected - epub_indexes)][
                :100
            ],
            "unexpected_page_markers_1based": [i + 1 for i in sorted(epub_indexes - expected)][
                :100
            ],
        },
        "notice": "Markers are not reader screen pages or proof of OCR accuracy; other EPUBs may use different markers. The EPUB is not independently bound to this project by this comparison.",
    }
