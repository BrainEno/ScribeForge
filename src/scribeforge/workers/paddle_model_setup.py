from __future__ import annotations

import argparse
import hashlib
import shutil
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


class ModelIntegrityError(RuntimeError):
    """A downloaded or cached model does not match its locked identity."""


@dataclass(frozen=True, slots=True)
class ModelAsset:
    name: str
    repository: str
    revision: str
    weight_path: str
    weight_sha256: str
    weight_bytes: int
    required_paths: tuple[str, ...]


PADDLE_MODELS = (
    ModelAsset(
        name="PP-OCRv4_mobile_det",
        repository="PaddlePaddle/PP-OCRv4_mobile_det",
        revision="3cc09f3a5b424e8e010abc7a4271aea12999c2f7",
        weight_path="inference.pdiparams",
        weight_sha256="54a85087b4d31fa3ea4e4aba100169a1ec3e3274cd3352b9068b3cfccbca7829",
        weight_bytes=4_692_937,
        required_paths=(
            "config.json",
            "inference.json",
            "inference.yml",
            "inference.pdiparams",
        ),
    ),
    ModelAsset(
        name="PP-OCRv4_mobile_rec",
        repository="PaddlePaddle/PP-OCRv4_mobile_rec",
        revision="a2deb05cb05813be2da3365672881aa496d1452b",
        weight_path="inference.pdiparams",
        weight_sha256="948b8d2ca6ad4bd7b64c0ee3fb838a06db87d4cf57137173ce409c995df21012",
        weight_bytes=10_766_823,
        required_paths=(
            "config.json",
            "inference.json",
            "inference.yml",
            "inference.pdiparams",
        ),
    ),
)

SnapshotDownloader = Callable[[str, str, Path], None]


def _download_snapshot(repository: str, revision: str, destination: Path) -> None:
    from huggingface_hub import snapshot_download  # type: ignore[import-not-found]

    snapshot_download(
        repo_id=repository,
        revision=revision,
        local_dir=str(destination),
    )


def _integrity_problem(asset: ModelAsset, root: Path) -> str | None:
    for relative in asset.required_paths:
        if not (root / relative).is_file():
            return f"missing required file: {relative}"
    weight = root / asset.weight_path
    if weight.stat().st_size != asset.weight_bytes:
        return (
            f"weight size mismatch for {asset.name}: expected "
            f"{asset.weight_bytes}, got {weight.stat().st_size}"
        )
    digest = hashlib.sha256()
    with weight.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != asset.weight_sha256:
        return f"weight SHA-256 mismatch for {asset.name}"
    return None


def verify_paddle_model(asset: ModelAsset, root: Path) -> None:
    """Raise unless every required file and the locked weight digest matches."""

    problem = _integrity_problem(asset, root)
    if problem is not None:
        raise ModelIntegrityError(problem)


def prepare_paddle_models(
    models_root: Path,
    *,
    assets: Sequence[ModelAsset] = PADDLE_MODELS,
    snapshot_downloader: SnapshotDownloader = _download_snapshot,
) -> dict[str, Path]:
    """Download immutable Paddle snapshots, verify, then transactionally publish."""

    models_root.mkdir(parents=True, exist_ok=True)
    prepared: dict[str, Path] = {}
    for asset in assets:
        target = models_root / asset.name
        if target.is_dir() and _integrity_problem(asset, target) is None:
            prepared[asset.name] = target
            continue

        staging = models_root / f".{asset.name}.staging-{uuid.uuid4().hex}"
        backup = models_root / f".{asset.name}.backup-{uuid.uuid4().hex}"
        _remove(staging)
        _remove(backup)
        try:
            snapshot_downloader(asset.repository, asset.revision, staging)
            verify_paddle_model(asset, staging)
            had_previous = target.exists()
            if had_previous:
                target.replace(backup)
            try:
                staging.replace(target)
            except BaseException:
                if had_previous and backup.exists() and not target.exists():
                    backup.replace(target)
                raise
            if had_previous:
                _remove(backup)
        finally:
            _remove(staging)
        prepared[asset.name] = target
    return prepared


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download and verify locked PP-OCRv4 model snapshots."
    )
    parser.add_argument("--models-dir", type=Path, required=True)
    args = parser.parse_args()
    paths = prepare_paddle_models(args.models_dir)
    for name, path in paths.items():
        print(f"{name}={path}", flush=True)


if __name__ == "__main__":
    main()
