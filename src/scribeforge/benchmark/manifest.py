from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    id: str
    image: str
    ground_truth: str
    language: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    schema_version: int
    cases: tuple[BenchmarkCase, ...]


def load_manifest(path: Path) -> BenchmarkManifest:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("unsupported benchmark schema")

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list):
        raise TypeError("benchmark cases must be a list")

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
        cases.append(
            BenchmarkCase(
                id=case_id,
                image=str(item["image"]),
                ground_truth=str(item["ground_truth"]),
                language=str(item["language"]),
                tags=tuple(str(tag) for tag in tags),
            )
        )
    return BenchmarkManifest(schema_version=1, cases=tuple(cases))
