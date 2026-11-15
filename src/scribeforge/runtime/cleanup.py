"""Cleanup of disposable runtime artifacts (issue #214).

First-run setup is transactional: incomplete backends are built in
``.<name>.staging-*`` directories and swapped into place, leaving
``.<name>.previous-*`` behind only when a publish fails mid-way. After a
cancelled or failed run these directories are dead weight. This module
removes them — and optionally the download cache — and reports exactly how
many bytes were freed, so the desktop UI can offer an actionable "清理" button
when the disk preflight says the drive is too full.

Kept by default (they make retries cheap): models, cache, tools, ready
environments and ``runtime-state.json``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

STAGING_PREFIX = "."
STAGING_MARKER = ".staging-"
PREVIOUS_MARKER = ".previous-"


def _dir_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def clean_runtime_artifacts(
    runtime_root: Path,
    *,
    include_cache: bool = False,
    include_models: bool = False,
) -> dict[str, object]:
    """Remove disposable artifacts under ``runtime_root``.

    Returns a JSON-friendly report: ``freed_bytes``, the removed paths and
    what was deliberately kept. Never touches ready backend environments or
    the state file unless explicitly asked via ``include_models``.
    """
    root = Path(runtime_root)
    removed: list[str] = []
    freed = 0

    if root.is_dir():
        from scribeforge.runtime.models import RuntimeLayout

        layout = RuntimeLayout(root)
        # Staging/previous environments live both at the top level and inside
        # the environments directory (envs/.<name>.staging-*), so scan both.
        scan_dirs = [root, layout.environment_dir("__probe__").parent]
        for directory in scan_dirs:
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                name = child.name
                disposable = STAGING_MARKER in name or PREVIOUS_MARKER in name
                if not disposable or not child.is_dir():
                    continue
                size = _dir_size(child)
                shutil.rmtree(child, ignore_errors=True)
                if not child.exists():
                    removed.append(str(child))
                    freed += size

        optional_targets: list[Path] = []
        if include_cache:
            optional_targets.append(layout.cache_dir)
        if include_models:
            optional_targets.append(layout.models_dir)
        for target in optional_targets:
            if not target.is_dir():
                continue
            size = _dir_size(target)
            shutil.rmtree(target, ignore_errors=True)
            if not target.exists():
                removed.append(str(target))
                freed += size

    return {
        "runtime_root": str(root),
        "freed_bytes": freed,
        "removed": removed,
        "kept": _kept_artifacts(root),
        "include_cache": include_cache,
        "include_models": include_models,
    }


def _kept_artifacts(runtime_root: Path) -> list[str]:
    """Names of top-level artifacts the cleanup deliberately preserves."""
    from scribeforge.runtime.models import RuntimeLayout

    root = Path(runtime_root)
    layout = RuntimeLayout(root)
    kept = [
        "runtime-state.json",
        layout.tools_dir.name,
        "envs",
    ]
    if layout.models_dir.is_dir():
        kept.append(layout.models_dir.name)
    if layout.cache_dir.is_dir():
        kept.append(layout.cache_dir.name)
    return kept
