from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DIRECTIONS = ("horizontal-lr", "horizontal-rl", "vertical-rl", "vertical-lr")
_GROUND_TRUTH_REVIEW_STATUSES = ("unreviewed", "single-pass", "double-proofread")


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    id: str
    image: str
    ground_truth: str
    language: str
    tags: tuple[str, ...]
    #: Declared page writing direction, when the corpus owner knows it. Used only
    #: to split the report; the pipeline still detects direction from geometry, so
    #: a wrong declaration here cannot corrupt a run — it would only mislabel a row.
    direction: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    schema_version: int
    cases: tuple[BenchmarkCase, ...]
    #: Declared transcription review level. ``None`` keeps older manifests honest:
    #: absence is not evidence that the reference text was proofread.
    ground_truth_review_status: str | None = None
    ground_truth_note: str | None = None
    #: Digest of the exact manifest bytes that produced this model instance.
    #: Directly constructed manifests have no file provenance.
    manifest_sha256: str | None = field(default=None, compare=False, repr=False)


def load_manifest(path: Path) -> BenchmarkManifest:
    manifest_bytes = path.read_bytes()
    raw: Any = json.loads(manifest_bytes.decode("utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("unsupported benchmark schema")

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list):
        raise TypeError("benchmark cases must be a list")

    review_status = raw.get("ground_truth_review_status")
    if review_status is not None:
        if not isinstance(review_status, str):
            raise TypeError("ground_truth_review_status must be a string")
        if review_status not in _GROUND_TRUTH_REVIEW_STATUSES:
            raise ValueError(f"unknown ground-truth review status: {review_status}")
    ground_truth_note = raw.get("ground_truth_note")
    if ground_truth_note is not None and not isinstance(ground_truth_note, str):
        raise TypeError("ground_truth_note must be a string")

    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for item in raw_cases:
        if not isinstance(item, dict):
            raise TypeError("benchmark case must be an object")
        case_id = str(item["id"])
        if case_id in seen:
            raise ValueError(f"duplicate benchmark case id: {case_id}")
        seen.add(case_id)
        tags = item.get("tags", [])
        if not isinstance(tags, list):
            raise TypeError("benchmark tags must be a list")
        direction = item.get("direction")
        if direction is not None:
            if not isinstance(direction, str):
                raise TypeError("benchmark direction must be a string")
            if direction not in _DIRECTIONS:
                raise ValueError(f"unknown benchmark direction: {direction}")
        cases.append(
            BenchmarkCase(
                id=case_id,
                image=str(item["image"]),
                ground_truth=str(item["ground_truth"]),
                language=str(item["language"]),
                tags=tuple(str(tag) for tag in tags),
                direction=direction,
            )
        )
    return BenchmarkManifest(
        schema_version=1,
        cases=tuple(cases),
        ground_truth_review_status=review_status,
        ground_truth_note=ground_truth_note,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
