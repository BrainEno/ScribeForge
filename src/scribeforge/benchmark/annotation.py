"""Corpus annotation support for issues #62/#68.

Building the 30-50 page multilingual corpus is a human transcription job; this
module carries the mechanical, checkable half: per-page metadata overrides
(language/direction/tags can differ per page of one PDF), and an
annotation-status report that shows exactly what a corpus is still missing
(per-language counts, directions, empty transcriptions) together with the
quality-gate verdict, so corpus growth is measurable instead of anecdotal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scribeforge.benchmark.manifest import _DIRECTIONS, load_manifest
from scribeforge.benchmark.quality_gate import (
    QualityGateSettings,
    check_corpus_quality,
    render_quality_report,
)


@dataclass(frozen=True, slots=True)
class PageOverride:
    """Per-page metadata override from a page-spec JSON file."""

    language: str | None = None
    direction: str | None = None
    tags: tuple[str, ...] = ()


def load_page_spec(path: Path | None) -> dict[int, PageOverride]:
    """Load ``{"pages": {"<page>": {"language": ..., "direction": ..., "tags": [...]}}}``.

    ``None`` means no overrides. Unknown directions, non-integer page keys and
    non-list tags are rejected loudly: a corpus manifest is evidence, so a
    malformed spec must fail the build, not silently mislabel pages.
    """

    if path is None:
        return {}
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("page spec must be a JSON object")
    pages = raw.get("pages", {})
    if not isinstance(pages, dict):
        raise TypeError("page spec 'pages' must be an object")
    spec: dict[int, PageOverride] = {}
    for key, value in pages.items():
        try:
            page_index = int(key)
        except (TypeError, ValueError):
            raise ValueError(f"page spec keys must be integer page indexes, got {key!r}") from None
        if not isinstance(value, dict):
            raise TypeError(f"page spec for page {page_index} must be an object")
        language = value.get("language")
        if language is not None and not isinstance(language, str):
            raise TypeError(f"page {page_index} language must be a string")
        direction = value.get("direction")
        if direction is not None and (
            not isinstance(direction, str) or direction not in _DIRECTIONS
        ):
            raise ValueError(f"unknown direction for page {page_index}: {direction!r}")
        tags = value.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise TypeError(f"page {page_index} tags must be a list of strings")
        spec[page_index] = PageOverride(language=language, direction=direction, tags=tuple(tags))
    return spec


def resolve_case_metadata(
    *,
    page_index: int,
    default_language: str,
    default_direction: str | None,
    default_tags: tuple[str, ...],
    spec: dict[int, PageOverride],
) -> tuple[str, str | None, tuple[str, ...]]:
    """Merge a page override over the builder defaults.

    ``None`` fields in the override mean "keep the default", so a spec can
    change only the language of one page without restating direction/tags.
    """

    override = spec.get(page_index)
    if override is None:
        return default_language, default_direction, default_tags
    return (
        override.language if override.language is not None else default_language,
        override.direction if override.direction is not None else default_direction,
        override.tags if override.tags else default_tags,
    )


@dataclass(frozen=True, slots=True)
class AnnotationStatus:
    """What a corpus directory still needs before it can support claims."""

    corpus_root: str
    case_count: int
    languages: dict[str, int]
    directions: dict[str, int]
    empty_ground_truths: tuple[str, ...]
    missing_images: tuple[str, ...]
    review_status: str
    license: str | None
    gate_errors: int
    gate_warnings: int

    @property
    def annotation_complete(self) -> bool:
        """True when every case has an image and a non-empty transcription."""

        return not self.empty_ground_truths and not self.missing_images


def annotation_status(root: Path) -> AnnotationStatus:
    """Summarize one corpus directory (``root`` holds ``manifest.json``)."""

    manifest = load_manifest(root / "manifest.json")
    languages: dict[str, int] = {}
    directions: dict[str, int] = {}
    empty: list[str] = []
    missing: list[str] = []
    for case in manifest.cases:
        language = case.language.strip().lower() or "(blank)"
        languages[language] = languages.get(language, 0) + 1
        direction = case.direction or "(undeclared)"
        directions[direction] = directions.get(direction, 0) + 1
        truth = root / case.ground_truth
        if not truth.is_file() or not truth.read_text(encoding="utf-8").strip():
            empty.append(case.id)
        if not (root / case.image).is_file():
            missing.append(case.id)
    issues = check_corpus_quality(manifest, settings=QualityGateSettings(), root=root)
    errors = sum(1 for issue in issues if issue.severity == "error")
    return AnnotationStatus(
        corpus_root=str(root),
        case_count=len(manifest.cases),
        languages=dict(sorted(languages.items())),
        directions=dict(sorted(directions.items())),
        empty_ground_truths=tuple(sorted(empty)),
        missing_images=tuple(sorted(missing)),
        review_status=manifest.ground_truth_review_status or "not declared",
        license=manifest.license,
        gate_errors=errors,
        gate_warnings=len(issues) - errors,
    )


def render_annotation_status(status: AnnotationStatus) -> str:
    """Deterministic Markdown: coverage tables, annotation gaps, gate verdict."""

    manifest_path = Path(status.corpus_root) / "manifest.json"
    manifest = load_manifest(manifest_path)
    issues = check_corpus_quality(
        manifest, settings=QualityGateSettings(), root=Path(status.corpus_root)
    )
    lines = [
        "# Corpus annotation status",
        "",
        f"- corpus: `{status.corpus_root}`",
        f"- cases: {status.case_count}",
        f"- license: {status.license or 'NOT DECLARED'}",
        f"- ground-truth review: `{status.review_status}`",
        f"- annotation complete: {'yes' if status.annotation_complete else 'NO'}",
        "",
        "## Pages by language",
        "",
        "| Language | Pages |",
        "| --- | ---: |",
    ]
    for language, count in status.languages.items():
        lines.append(f"| {language} | {count} |")
    lines += ["", "## Pages by direction", "", "| Direction | Pages |", "| --- | ---: |"]
    for direction, count in status.directions.items():
        lines.append(f"| {direction} | {count} |")
    lines += ["", "## Annotation gaps", ""]
    if status.empty_ground_truths:
        lines.append(
            f"- empty/missing ground truth ({len(status.empty_ground_truths)}): "
            + ", ".join(f"`{case}`" for case in status.empty_ground_truths)
        )
    else:
        lines.append("- all ground-truth files are present and non-empty")
    if status.missing_images:
        lines.append(
            f"- missing images ({len(status.missing_images)}): "
            + ", ".join(f"`{case}`" for case in status.missing_images)
        )
    else:
        lines.append("- all case images are present")
    lines += [
        "",
        "## Quality gate",
        "",
        (
            f"- verdict: {'FAIL' if status.gate_errors else 'PASS'} "
            f"({status.gate_errors} error(s), {status.gate_warnings} warning(s))"
        ),
        "",
    ]
    lines.append(render_quality_report(manifest, issues).split("\n", 1)[1].strip())
    lines.append("")
    return "\n".join(lines)


def annotation_status_json(status: AnnotationStatus) -> str:
    """Stable schema-v1 JSON rendering for CI consumption."""

    payload: dict[str, Any] = {
        "schema_version": 1,
        "corpus_root": status.corpus_root,
        "case_count": status.case_count,
        "languages": status.languages,
        "directions": status.directions,
        "empty_ground_truths": list(status.empty_ground_truths),
        "missing_images": list(status.missing_images),
        "review_status": status.review_status,
        "license": status.license,
        "annotation_complete": status.annotation_complete,
        "gate_errors": status.gate_errors,
        "gate_warnings": status.gate_warnings,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
