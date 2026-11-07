from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import scripts.record_paddle_smoke_evidence as evidence_module
from scribeforge.runtime.environment import paddle_model_root
from scribeforge.runtime.models import RuntimeLayout
from scribeforge.workers.paddle_model_setup import LockedFile, ModelAsset


@pytest.mark.parametrize("os_name", ["linux", "windows"])
def test_record_real_smoke_evidence_uses_production_model_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    repository = tmp_path / "repository"
    runtime_root = tmp_path / "runtime"
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    layout = RuntimeLayout(runtime_root)
    model_root = paddle_model_root(layout) / "test-det"
    model_root.mkdir(parents=True)
    payloads = {
        "inference.pdiparams": b"real-weight-bytes",
        "config.json": b"config",
        "inference.json": b"json",
        "inference.yml": b"yaml",
    }
    for relative, payload in payloads.items():
        (model_root / relative).write_bytes(payload)
    weight = payloads["inference.pdiparams"]
    asset = ModelAsset(
        name="test-det",
        repository="test/model",
        revision="test-revision",
        weight_path="inference.pdiparams",
        weight_sha256=hashlib.sha256(weight).hexdigest(),
        weight_bytes=len(weight),
        required_paths=tuple(payloads),
        metadata_files=tuple(
            LockedFile(
                relative,
                hashlib.sha256(payloads[relative]).hexdigest(),
                len(payloads[relative]),
            )
            for relative in ("config.json", "inference.json", "inference.yml")
        ),
    )
    monkeypatch.setattr(evidence_module, "PADDLE_MODELS", (asset,))

    lock = repository / "src/scribeforge/runtime/locks/paddleocr-cpu-py312-universal.txt"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(b"locked requirements")
    (runtime_root / "runtime-state.json").write_text('{"state": "ready"}\n', encoding="utf-8")
    (evidence_dir / "synthetic-paddle-smoke.png").write_bytes(b"synthetic image")

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert argv[0] == str(layout.python_executable("paddleocr", os_name))
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps({"python": "3.12.16", "paddleocr": "3.3.0"}),
        )

    monkeypatch.setattr(evidence_module.subprocess, "run", fake_run)

    report = evidence_module.record_evidence(
        evidence_dir, runtime_root, repository, os_name=os_name
    )

    assert report["platform"] == os_name
    assert report["installed_distributions"]["paddleocr"] == "3.3.0"
    assert report["accuracy_evaluation"]["performed"] is False
    assert report["models"][0]["revision"] == "test-revision"
    files = {item["path"]: item for item in report["models"][0]["files"]}
    assert set(files) == set(payloads)
    assert files["inference.pdiparams"]["sha256"] == hashlib.sha256(weight).hexdigest()
    assert report["lock_file"]["sha256"] == hashlib.sha256(b"locked requirements").hexdigest()
    assert json.loads((evidence_dir / "model-evidence.json").read_text()) == report
    assert (evidence_dir / "runtime-state.json").read_text() == '{"state": "ready"}\n'
