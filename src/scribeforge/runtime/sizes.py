"""Disk-space budgeting for first-run runtime setup (issue #214).

The planner knows *what* gets installed per backend (uv, a private Python,
locked wheels, model downloads) but not how many bytes that is on a given
machine — wheel sizes differ between CPU and CUDA builds. This module keeps
curated, clearly-labelled estimates per backend plus a headroom factor, and
answers the one question users actually ask before clicking "初始化环境":
*does the drive holding the runtime root have enough room?*

All numbers are estimates (预估值); the UI must present them as such and
never as exact measurements.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

GiB = 1024**3
MiB = 1024**2

#: Extra room on top of the raw estimate so a download that lands slightly
#: above the estimate (or a partially-written temp file) does not fail late.
HEADROOM_FACTOR = 1.10


@dataclass(frozen=True)
class BackendSizeEstimate:
    """Three-phase size estimate for one backend (issue #214).

    ``download`` = bytes pulled from the network (wheels + models),
    ``install`` = bytes the installed environment occupies on disk,
    ``models`` = bytes of downloaded model weights on disk.
    """

    name: str
    download_bytes: int
    install_bytes: int
    model_bytes: int
    note: str = ""

    @property
    def total_bytes(self) -> int:
        return self.download_bytes + self.install_bytes + self.model_bytes


#: Curated per-backend estimates. Torch/Paddle CUDA wheels dominate the
#: install phase on GPU machines; on CPU-only machines the CUDA wheels are
#: skipped, so the UI shows the lower "cpu" figures instead.
BACKEND_SIZE_ESTIMATES: dict[str, BackendSizeEstimate] = {
    "mineru": BackendSizeEstimate(
        name="mineru",
        download_bytes=6 * GiB,
        install_bytes=8 * GiB,
        model_bytes=4 * GiB,
        note="含 PyTorch 与 MinerU 标准模型（GPU 版体积更大）",
    ),
    "paddleocr": BackendSizeEstimate(
        name="paddleocr",
        download_bytes=600 * MiB,
        install_bytes=1 * GiB,
        model_bytes=100 * MiB,
        note="PP-OCRv4 移动版模型；CPU 版体积明显小于 GPU 版",
    ),
    "paddleocr-vl": BackendSizeEstimate(
        name="paddleocr-vl",
        download_bytes=8 * GiB,
        install_bytes=10 * GiB,
        model_bytes=6 * GiB,
        note="可选技术路线 R1，多卡模型体积较大",
    ),
    "surya": BackendSizeEstimate(
        name="surya",
        download_bytes=3 * GiB,
        install_bytes=4 * GiB,
        model_bytes=2 * GiB,
        note="可选技术路线 R2，版面分析模型",
    ),
}

#: Used when a backend has no curated entry (forward compatibility).
FALLBACK_SIZE_ESTIMATE = BackendSizeEstimate(
    name="unknown",
    download_bytes=1 * GiB,
    install_bytes=2 * GiB,
    model_bytes=1 * GiB,
    note="未收录的后端，按保守值估算",
)

#: Binaries/tools (uv) downloaded once into the shared tools directory.
TOOLS_DOWNLOAD_BYTES = 30 * MiB


class DiskUsage(NamedTuple):
    """Drive usage triple; decoupled from the exact shutil return type."""

    total: int
    used: int
    free: int


DiskUsageProbe = Callable[[Path], DiskUsage]


def estimate_backend(name: str) -> BackendSizeEstimate:
    """Return the curated estimate for ``name`` or a conservative fallback."""
    estimate = BACKEND_SIZE_ESTIMATES.get(name)
    if estimate is not None:
        return estimate
    return BackendSizeEstimate(
        name=name,
        download_bytes=FALLBACK_SIZE_ESTIMATE.download_bytes,
        install_bytes=FALLBACK_SIZE_ESTIMATE.install_bytes,
        model_bytes=FALLBACK_SIZE_ESTIMATE.model_bytes,
        note=FALLBACK_SIZE_ESTIMATE.note,
    )


def estimate_payload(backend_names: Iterable[str]) -> dict[str, dict[str, int | str]]:
    """Per-backend three-phase estimates as a JSON-friendly mapping."""
    payload: dict[str, dict[str, int | str]] = {}
    for name in backend_names:
        estimate = estimate_backend(name)
        payload[name] = {
            "download_bytes": estimate.download_bytes,
            "install_bytes": estimate.install_bytes,
            "model_bytes": estimate.model_bytes,
            "total_bytes": estimate.total_bytes,
            "note": estimate.note,
        }
    return payload


def required_bytes(backend_names: Iterable[str], *, reuse_models: bool = False) -> int:
    """Total bytes the runtime root drive should have free, headroom included."""
    names = list(backend_names)
    estimates = [estimate_backend(name) for name in names]
    total = sum(item.total_bytes - (item.model_bytes if reuse_models else 0) for item in estimates)
    total += TOOLS_DOWNLOAD_BYTES if names else 0
    return int(total * HEADROOM_FACTOR)


def disk_usage(path: Path) -> DiskUsage:
    """Default disk-usage probe; injectable in tests."""
    usage = shutil.disk_usage(path)
    return DiskUsage(total=usage.total, used=usage.used, free=usage.free)


def preflight(
    runtime_root: Path,
    backend_names: Iterable[str],
    *,
    usage_probe: DiskUsageProbe = disk_usage,
    reuse_models: bool = False,
) -> dict[str, object]:
    """Decide whether first-run setup can start on the runtime-root drive.

    Returns a JSON-friendly dict consumed by ``runtime-status --json`` and
    rendered by the desktop UI before any download begins (issue #214).
    """
    names = list(backend_names)
    root = Path(runtime_root)
    # shutil.disk_usage needs an existing path; walk up to the nearest
    # existing ancestor so a not-yet-created runtime root still yields the
    # drive's real numbers.
    probe_path = root
    while not probe_path.exists() and probe_path.parent != probe_path:
        probe_path = probe_path.parent
    if not probe_path.exists():
        probe_path = Path(root.anchor or ".")
    usage = usage_probe(probe_path)
    required = required_bytes(names, reuse_models=reuse_models)
    free = int(usage.free)
    sufficient = free >= required
    missing = max(0, required - free)
    advice = (
        f"预计需要约 {format_bytes(required)}（含 {int(HEADROOM_FACTOR * 100) - 100}% 余量），"
        f"当前盘剩余 {format_bytes(free)}，可以开始安装。"
        if sufficient
        else (
            f"预计需要约 {format_bytes(required)}（含 {int(HEADROOM_FACTOR * 100) - 100}% 余量），"
            f"当前盘剩余 {format_bytes(free)}，还差约 {format_bytes(missing)}。"
            "请清理磁盘、更换运行目录（--runtime-root 指到更大的盘），或只安装核心引擎后再按需安装可选引擎。"
        )
    )
    return {
        "path": str(probe_path),
        "runtime_root": str(root),
        "free_bytes": free,
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "required_bytes": required,
        "headroom_factor": HEADROOM_FACTOR,
        "sufficient": sufficient,
        "missing_bytes": missing,
        "backends": estimate_payload(names),
        "tools_download_bytes": TOOLS_DOWNLOAD_BYTES if names else 0,
        "advice": advice,
        "estimated": True,
    }


def format_bytes(value: int) -> str:
    """Human-readable byte size, e.g. ``12.5 GB``."""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
