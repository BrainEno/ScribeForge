"""Benchmark corpus quality gate (issues #62 / #68).

The project's benchmark README demands 30-50 representative pages with manually
verified ground truth spanning clear/poor scans, chapter starts, punctuation,
footnotes, images, headers/footers and Chinese/English/Japanese samples. This
module turns those prose requirements into machine-checked rules: a corpus that
is too small, single-language, or missing the hard layout cases (Japanese
vertical text, English two-column) produces explicit issues instead of a
quietly meaningless engine ranking.

The gate only reads declared manifest metadata; it never infers language or
layout from the page images.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from scribeforge.benchmark.manifest import BenchmarkManifest

#: Layout tags that count as a two-column English sample (checked as substrings).
_TWO_COLUMN_TAGS = ("two-column", "双栏", "multi-column", "双欄")

#: License strings accepted without a warning (SPDX ids plus public-domain).
#: Anything matching an SPDX-id shape (e.g. CC-BY-ND-4.0) also counts as
#: standard; obviously non-redistributable strings (BSL, all-rights-reserved)
#: still warn.
_SPDX_LIKE = re.compile(r"^[A-Z][A-Za-z0-9]+(-[A-Za-z0-9.]+)*-\d+\.\d+$")
#: SPDX-shaped but non-redistributable licenses that must fail the gate.
_NON_REDISTRIBUTABLE_LICENSES = frozenset(
    {"bsl-1.0", "bsl-1.1", "all-rights-reserved", "proprietary", "proprietary-internal", "ssl"}
)
_STANDARD_LICENSES = frozenset(
    {
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "CC-BY-3.0",
        "CC-BY-SA-3.0",
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "GPL-3.0-only",
        "GPL-2.0-only",
        "LGPL-2.1-only",
        "LGPL-3.0-only",
        "MPL-2.0",
        "EPL-2.0",
        "AGPL-3.0-only",
        "OGL-UK-3.0",
        "public-domain",
        "Public Domain",
        "CC-PDDC",
        "Unlicense",
    }
)


class _ScoredLike(Protocol):
    @property
    def language(self) -> str: ...

    @property
    def tags(self) -> tuple[str, ...]: ...

    @property
    def direction(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class QualityIssue:
    severity: str  # "error" blocks the corpus; "warning" is advisory
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class QualityGateSettings:
    #: Minimum total page count (benchmark README asks for 30-50).
    min_total_pages: int = 30
    #: Minimum pages per required language (#68 asks for >= 8 each of en/ja).
    min_pages_per_language: int = 8
    #: Languages the project claims as its initial target (README).
    required_languages: tuple[str, ...] = ("zh", "en", "ja")
    #: Require at least one Japanese vertical-writing sample.
    require_vertical_sample: bool = True
    #: Require at least one English two-column sample.
    require_two_column_sample: bool = True
    #: Require every case to carry at least one layout tag.
    require_layout_tag_per_case: bool = True
    #: Require a manifest-level license declaration (#62: no copyrighted books).
    require_license: bool = True
    #: Require every case to declare where the scan came from.
    require_source_per_case: bool = False
    #: Require every case image file to exist under the corpus root.
    check_images: bool = False

    def __post_init__(self) -> None:
        if self.min_total_pages < 1:
            raise ValueError(f"min_total_pages must be >= 1, got {self.min_total_pages}")
        if self.min_pages_per_language < 1:
            raise ValueError(
                f"min_pages_per_language must be >= 1, got {self.min_pages_per_language}"
            )
        if not self.required_languages:
            raise ValueError("required_languages must not be empty")


def check_corpus_quality(
    manifest: BenchmarkManifest,
    *,
    settings: QualityGateSettings | None = None,
    root: Path | None = None,
) -> tuple[QualityIssue, ...]:
    """Check a manifest against the corpus quality rules.

    Returns all issues found; ``error`` severity means the corpus cannot
    support trustworthy claims, ``warning`` means a declared coverage gap.
    """
    settings = settings or QualityGateSettings()
    cases = manifest.cases
    issues: list[QualityIssue] = []

    license = (manifest.license or "").strip()
    if settings.require_license and not license:
        issues.append(
            QualityIssue(
                "error",
                "license-missing",
                "manifest declares no license; copyrighted material must not be committed (#62)",
            )
        )
    elif license.lower() in _NON_REDISTRIBUTABLE_LICENSES:
        issues.append(
            QualityIssue(
                "error",
                "license-not-redistributable",
                f"license {license!r} is not redistributable; copyrighted material must not "
                "be committed to the benchmark corpus (#62)",
            )
        )
    elif license not in _STANDARD_LICENSES and not _SPDX_LIKE.match(license):
        issues.append(
            QualityIssue(
                "warning",
                "license-not-standard",
                f"license {license!r} is not a recognized SPDX id or public-domain marker; "
                "verify it is redistributable before sharing the corpus",
            )
        )
    if settings.require_source_per_case:
        missing_source = sum(1 for case in cases if not (case.source or "").strip())
        if missing_source:
            issues.append(
                QualityIssue(
                    "warning",
                    "source-missing",
                    f"{missing_source} of {len(cases)} case(s) declare no source/provenance",
                )
            )
    if settings.check_images and root is not None:
        for case in cases:
            image_path = Path(case.image)
            if image_path.is_absolute() or ".." in image_path.parts:
                issues.append(
                    QualityIssue(
                        "error",
                        "image-path-unsafe",
                        f"case {case.id!r} image {case.image!r} must be a relative path "
                        "inside the corpus root",
                    )
                )
                continue
            if not (root / case.image).is_file():
                issues.append(
                    QualityIssue(
                        "error",
                        "image-missing",
                        f"case {case.id!r} image not found under corpus root {root}",
                    )
                )
    if license and manifest.license_url:
        url = manifest.license_url.strip()
        if not url.startswith(("https://", "http://")):
            issues.append(
                QualityIssue(
                    "warning",
                    "license-url-suspicious",
                    f"license_url {url!r} is not an http(s) URL",
                )
            )

    if len(cases) < settings.min_total_pages:
        issues.append(
            QualityIssue(
                "error",
                "corpus-too-small",
                f"corpus has {len(cases)} page(s), below the minimum of "
                f"{settings.min_total_pages}; engine rankings from this sample are unstable",
            )
        )

    languages: dict[str, int] = {}
    images: dict[str, str] = {}
    vertical_ja = 0
    two_column_en = 0
    for case in cases:
        language = case.language.strip().lower()
        if not language:
            issues.append(
                QualityIssue(
                    "error", "blank-language", f"case {case.id!r} declares a blank language"
                )
            )
            continue
        languages[language] = languages.get(language, 0) + 1
        if case.image in images:
            issues.append(
                QualityIssue(
                    "error",
                    "duplicate-image",
                    f"cases {images[case.image]!r} and {case.id!r} share image {case.image!r}",
                )
            )
        else:
            images[case.image] = case.id
        if not case.ground_truth.strip():
            issues.append(
                QualityIssue(
                    "error", "empty-ground-truth", f"case {case.id!r} has empty ground truth"
                )
            )
        if settings.require_layout_tag_per_case and not case.tags:
            issues.append(
                QualityIssue(
                    "warning",
                    "layout-tag-missing",
                    f"case {case.id!r} carries no layout tag, so per-layout slicing skips it",
                )
            )
        if (
            language == "ja"
            and case.direction is not None
            and case.direction.startswith("vertical")
        ):
            vertical_ja += 1
        if language == "en" and any(tag in t for t in case.tags for tag in _TWO_COLUMN_TAGS):
            two_column_en += 1

    for language in settings.required_languages:
        count = languages.get(language, 0)
        if count == 0:
            issues.append(
                QualityIssue(
                    "error",
                    "language-missing",
                    f"required language {language!r} has no corpus case; claims about it are unverified",
                )
            )
        elif count < settings.min_pages_per_language:
            issues.append(
                QualityIssue(
                    "error",
                    "language-underrepresented",
                    f"language {language!r} has {count} page(s), below the minimum of "
                    f"{settings.min_pages_per_language}",
                )
            )
    for language in sorted(languages):
        if language not in settings.required_languages:
            issues.append(
                QualityIssue(
                    "warning",
                    "undeclared-language",
                    f"language {language!r} is scored but not in the required target set "
                    f"{list(settings.required_languages)}",
                )
            )

    if settings.require_vertical_sample and languages.get("ja", 0) > 0 and vertical_ja == 0:
        issues.append(
            QualityIssue(
                "error",
                "vertical-sample-missing",
                "Japanese cases exist but none declares a vertical direction; vertical "
                "reading-order accuracy is unverified",
            )
        )
    if settings.require_two_column_sample and languages.get("en", 0) > 0 and two_column_en == 0:
        issues.append(
            QualityIssue(
                "warning",
                "two-column-sample-missing",
                "English cases exist but none is tagged two-column; multi-column reading "
                "order is unverified",
            )
        )
    return tuple(issues)


def coverage_warnings(cases: Sequence[_ScoredLike]) -> list[str]:
    """Short human-readable coverage warnings for embedding in a report.

    Works on scored cases (which carry the manifest metadata), so the rendered
    benchmark report can flag coverage gaps next to the numbers themselves.
    """
    settings = QualityGateSettings()
    languages: dict[str, int] = {}
    vertical_ja = 0
    two_column_en = 0
    for case in cases:
        language = case.language.strip().lower()
        languages[language] = languages.get(language, 0) + 1
        if (
            language == "ja"
            and case.direction is not None
            and case.direction.startswith("vertical")
        ):
            vertical_ja += 1
        if language == "en" and any(tag in t for t in case.tags for tag in _TWO_COLUMN_TAGS):
            two_column_en += 1
    warnings: list[str] = []
    if len(cases) < settings.min_total_pages:
        warnings.append(
            f"corpus has only {len(cases)} page(s); rankings from it are unstable "
            f"(minimum recommended: {settings.min_total_pages})"
        )
    missing = [lang for lang in settings.required_languages if languages.get(lang, 0) == 0]
    if missing:
        warnings.append(
            f"no corpus cases for required language(s): {', '.join(missing)}; "
            "claims about them are unverified"
        )
    under = [
        f"{lang} ({languages.get(lang, 0)}/{settings.min_pages_per_language})"
        for lang in settings.required_languages
        if 0 < languages.get(lang, 0) < settings.min_pages_per_language
    ]
    if under:
        warnings.append(f"underrepresented language(s): {', '.join(under)}")
    if languages.get("ja", 0) > 0 and vertical_ja == 0:
        warnings.append(
            "no vertical-writing Japanese sample; reading-order accuracy for vertical text is unverified"
        )
    if languages.get("en", 0) > 0 and two_column_en == 0:
        warnings.append("no two-column English sample; multi-column reading order is unverified")
    return warnings


def render_quality_report(manifest: BenchmarkManifest, issues: Sequence[QualityIssue]) -> str:
    """Render the gate result as a Markdown report; PASS when no errors."""
    errors = [i for i in issues if i.severity == "error"]
    verdict = "FAIL" if errors else "PASS"
    lines = [
        "# Benchmark corpus quality gate",
        "",
        f"- cases: {len(manifest.cases)}",
        f"- license: {manifest.license or 'NOT DECLARED'}",
        f"- ground-truth review: {manifest.ground_truth_review_status or 'not declared'}",
        f"- verdict: **{verdict}**",
        f"- errors: {len(errors)}, warnings: {len(issues) - len(errors)}",
        "",
    ]
    if not issues:
        lines += ["No issues found.", ""]
        return "\n".join(lines)
    lines += ["| Severity | Code | Message |", "| --- | --- | --- |"]
    for issue in issues:
        message = issue.message.replace("|", "\\|")
        lines.append(f"| {issue.severity} | `{issue.code}` | {message} |")
    lines.append("")
    return "\n".join(lines)


def quality_gate_json(manifest: BenchmarkManifest, issues: Sequence[QualityIssue]) -> str:
    """Stable JSON rendering of the gate result for CI consumption."""
    languages: dict[str, int] = {}
    for case in manifest.cases:
        language = case.language.strip().lower() or "(blank)"
        languages[language] = languages.get(language, 0) + 1
    payload: dict[str, Any] = {
        "case_count": len(manifest.cases),
        "license": manifest.license,
        "license_url": manifest.license_url,
        "ground_truth_review_status": manifest.ground_truth_review_status,
        "languages": dict(sorted(languages.items())),
        "ok": not any(i.severity == "error" for i in issues),
        "issues": [{"severity": i.severity, "code": i.code, "message": i.message} for i in issues],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
