from __future__ import annotations

import errno
import hashlib
from pathlib import Path

import pytest

from scribeforge.workers.paddle_model_setup import (
    PADDLE_MODELS,
    LockedFile,
    ModelAsset,
    ModelIntegrityError,
    prepare_paddle_models,
    verify_paddle_model,
)


def _asset(root: Path, digest: str) -> ModelAsset:
    return ModelAsset(
        name="fixture-model",
        repository="ScribeForge/fixture",
        revision="a" * 40,
        weight_path="inference.pdiparams",
        weight_sha256=digest,
        weight_bytes=7,
        required_paths=("config.json", "inference.yml", "inference.pdiparams"),
        metadata_files=(
            LockedFile("config.json", hashlib.sha256(b"{}").hexdigest(), 2),
            LockedFile(
                "inference.yml",
                hashlib.sha256(b"model: fixture").hexdigest(),
                len(b"model: fixture"),
            ),
        ),
    )


def _write_snapshot(root: Path, weight: bytes) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.json").write_text("{}", encoding="utf-8")
    (root / "inference.yml").write_text("model: fixture", encoding="utf-8")
    (root / "inference.pdiparams").write_bytes(weight)


def test_verified_download_uses_fixed_revision_and_publishes_model(
    tmp_path: Path,
) -> None:
    weight = b"weights"
    asset = _asset(tmp_path, hashlib.sha256(weight).hexdigest())
    calls: list[tuple[str, str, Path]] = []

    def download(repository: str, revision: str, destination: Path) -> None:
        calls.append((repository, revision, destination))
        _write_snapshot(destination, weight)

    paths = prepare_paddle_models(
        tmp_path / "models", assets=(asset,), snapshot_downloader=download
    )

    assert calls[0][:2] == (asset.repository, asset.revision)
    assert paths[asset.name] == tmp_path / "models" / asset.name
    assert (paths[asset.name] / "inference.pdiparams").read_bytes() == weight
    assert not list((tmp_path / "models").glob("*.staging-*"))


def test_hash_mismatch_cleans_staging_and_preserves_previous_directory(
    tmp_path: Path,
) -> None:
    asset = _asset(tmp_path, hashlib.sha256(b"weights").hexdigest())
    previous = tmp_path / "models" / asset.name
    _write_snapshot(previous, b"old-v1")

    def bad_download(repository: str, revision: str, destination: Path) -> None:
        _write_snapshot(destination, b"badsum!")

    with pytest.raises(ModelIntegrityError, match="SHA-256"):
        prepare_paddle_models(
            tmp_path / "models", assets=(asset,), snapshot_downloader=bad_download
        )

    assert (previous / "inference.pdiparams").read_bytes() == b"old-v1"
    assert not list((tmp_path / "models").glob("*.staging-*"))


def test_valid_cached_model_does_not_download_again(tmp_path: Path) -> None:
    weight = b"weights"
    asset = _asset(tmp_path, hashlib.sha256(weight).hexdigest())
    _write_snapshot(tmp_path / "models" / asset.name, weight)

    def unexpected(repository: str, revision: str, destination: Path) -> None:
        raise AssertionError("a valid immutable model must not be downloaded again")

    paths = prepare_paddle_models(
        tmp_path / "models", assets=(asset,), snapshot_downloader=unexpected
    )

    assert paths[asset.name] == tmp_path / "models" / asset.name


def test_publish_failure_restores_previous_model(tmp_path: Path, monkeypatch) -> None:
    weight = b"weights"
    asset = _asset(tmp_path, hashlib.sha256(weight).hexdigest())
    previous = tmp_path / "models" / asset.name
    _write_snapshot(previous, b"old-v1")
    original_replace = Path.replace

    def fail_publish(self: Path, target: Path) -> Path:
        if ".staging-" in self.name and target == previous:
            raise OSError("simulated publish failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_publish)

    def download(repository: str, revision: str, destination: Path) -> None:
        _write_snapshot(destination, weight)

    with pytest.raises(OSError, match="publish failure"):
        prepare_paddle_models(tmp_path / "models", assets=(asset,), snapshot_downloader=download)

    assert (previous / "inference.pdiparams").read_bytes() == b"old-v1"


def test_worker_rechecks_cached_weight_before_returning_local_directories(
    tmp_path: Path, monkeypatch
) -> None:
    from scribeforge.workers import paddle_worker

    weight = b"weights"
    asset = _asset(tmp_path, hashlib.sha256(weight).hexdigest())
    model_root = tmp_path / "models"
    _write_snapshot(model_root / asset.name, weight)
    monkeypatch.setattr(paddle_worker, "PADDLE_MODELS", (asset,))
    monkeypatch.setenv("SCRIBEFORGE_PADDLE_MODELS_ROOT", str(model_root))

    paths = paddle_worker.locked_model_directories()
    assert paths == {asset.name: model_root / asset.name}

    (model_root / asset.name / "inference.pdiparams").write_bytes(b"badsum!")
    with pytest.raises(ModelIntegrityError, match="SHA-256"):
        paddle_worker.locked_model_directories()


def test_missing_required_file_is_rejected(tmp_path: Path) -> None:
    weight = b"weights"
    asset = _asset(tmp_path, hashlib.sha256(weight).hexdigest())

    def incomplete_download(repository: str, revision: str, destination: Path) -> None:
        _write_snapshot(destination, weight)
        (destination / "config.json").unlink()

    with pytest.raises(ModelIntegrityError, match="missing required file: config.json"):
        prepare_paddle_models(
            tmp_path / "models", assets=(asset,), snapshot_downloader=incomplete_download
        )


def test_weight_size_mismatch_is_rejected_before_hashing(tmp_path: Path) -> None:
    asset = _asset(tmp_path, hashlib.sha256(b"weights").hexdigest())

    def short_download(repository: str, revision: str, destination: Path) -> None:
        _write_snapshot(destination, b"tiny")

    with pytest.raises(ModelIntegrityError, match="size mismatch"):
        prepare_paddle_models(
            tmp_path / "models", assets=(asset,), snapshot_downloader=short_download
        )


@pytest.mark.parametrize(
    "failure",
    [
        OSError("simulated network unavailable"),
        OSError(errno.ENOSPC, "simulated disk full"),
    ],
    ids=["network-error", "disk-full"],
)
def test_download_failure_cleans_partial_staging_and_preserves_old_model(
    tmp_path: Path, failure: OSError
) -> None:
    asset = _asset(tmp_path, hashlib.sha256(b"weights").hexdigest())
    previous = tmp_path / "models" / asset.name
    _write_snapshot(previous, b"old-v1")

    def fail_download(repository: str, revision: str, destination: Path) -> None:
        _write_snapshot(destination, b"partial")
        raise failure

    with pytest.raises(OSError) as caught:
        prepare_paddle_models(
            tmp_path / "models", assets=(asset,), snapshot_downloader=fail_download
        )

    assert caught.value is failure
    assert (previous / "inference.pdiparams").read_bytes() == b"old-v1"
    assert not list((tmp_path / "models").glob("*.staging-*"))
    assert not list((tmp_path / "models").glob("*.backup-*"))


def test_permission_denied_during_publish_restores_old_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    weight = b"weights"
    asset = _asset(tmp_path, hashlib.sha256(weight).hexdigest())
    previous = tmp_path / "models" / asset.name
    _write_snapshot(previous, b"old-v1")
    original_replace = Path.replace
    denied = PermissionError(errno.EACCES, "simulated permission denied")

    def fail_publish(self: Path, target: Path) -> Path:
        if ".staging-" in self.name and target == previous:
            raise denied
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_publish)

    def download(repository: str, revision: str, destination: Path) -> None:
        _write_snapshot(destination, weight)

    with pytest.raises(PermissionError) as caught:
        prepare_paddle_models(tmp_path / "models", assets=(asset,), snapshot_downloader=download)

    assert caught.value is denied
    assert (previous / "inference.pdiparams").read_bytes() == b"old-v1"


def test_metadata_content_mismatch_is_rejected_even_when_weight_is_valid(
    tmp_path: Path,
) -> None:
    weight = b"weights"
    asset = _asset(tmp_path, hashlib.sha256(weight).hexdigest())
    model = tmp_path / "models" / asset.name
    _write_snapshot(model, weight)

    (model / "config.json").write_bytes(b"[]")

    with pytest.raises(ModelIntegrityError, match=r"config\.json.*SHA-256"):
        verify_paddle_model(asset, model)


def test_real_pp_ocr_v4_assets_pin_repository_revision_and_weight_digest() -> None:
    assert {asset.name for asset in PADDLE_MODELS} == {
        "PP-OCRv4_mobile_det",
        "PP-OCRv4_mobile_rec",
    }
    for asset in PADDLE_MODELS:
        assert asset.repository == f"PaddlePaddle/{asset.name}"
        assert len(asset.revision) == 40
        int(asset.revision, 16)
        assert len(asset.weight_sha256) == 64
        int(asset.weight_sha256, 16)
        assert asset.weight_bytes > 1_000_000
        assert asset.weight_path == "inference.pdiparams"


def test_real_pp_ocr_v4_assets_lock_every_metadata_file_by_size_and_sha256() -> None:
    expected = {
        "PP-OCRv4_mobile_det": {
            "config.json": (
                2_871,
                "25b95390ab0264dc56cd61f88f61e110700eec419bf799e235230a50ff7591a9",
            ),
            "inference.json": (
                229_777,
                "05feef1acb00aa4cd7362b15f7f501fc4f99d7b1fa73c1c871e0c7b1504b0f5c",
            ),
            "inference.yml": (
                903,
                "4f5bd0def48e20194d87d4c184a3ae3007a1299de7fed0ef763d3e7e873e77f6",
            ),
        },
        "PP-OCRv4_mobile_rec": {
            "config.json": (
                128_091,
                "26773626e73d17b4a6a6a39d15ee3a7820ddd05c3b71251894dc687e8174e81c",
            ),
            "inference.json": (
                217_718,
                "30f78ace76b2cce756abba97eb17f4c79e776d88ad68dd44a787a3db84b407a7",
            ),
            "inference.yml": (
                53_543,
                "1207e8ba3d3dad6b99115f7febf515f5ffc0d87ed77366d7a4b4b3f1709c005d",
            ),
        },
    }
    for asset in PADDLE_MODELS:
        metadata = {file.path: file for file in asset.metadata_files}
        assert set(metadata) == expected[asset.name].keys()
        for path, (size, digest) in expected[asset.name].items():
            assert metadata[path].size_bytes == size
            assert metadata[path].sha256 == digest
