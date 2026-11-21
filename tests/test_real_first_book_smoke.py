from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import smoke_real_first_book as smoke
from scripts.create_paddle_smoke_image import SMOKE_LINES


def test_fixture_review_only_selects_a_real_exact_candidate() -> None:
    reading = "ScribeForge  OCR 2026"
    assert smoke.choose_candidate({"primary_text": reading, "secondary_text": "wrong"}) == reading
    for item in (
        {"primary_text": "202", "secondary_text": "wrong"},
        {"primary_text": "ScribeForge", "secondary_text": "OCR 2026"},
        {"primary_text": None, "secondary_text": ""},
    ):
        with pytest.raises(RuntimeError, match="unique observed reading"):
            smoke.choose_candidate(item)


@pytest.mark.parametrize("bad_text", ["", "duplicate", "reversed"])
def test_spine_must_contain_each_reference_once_in_order(bad_text: str) -> None:
    smoke._check_text("\n".join(SMOKE_LINES))
    invalid = {
        "": "",
        "duplicate": " ".join((*SMOKE_LINES, SMOKE_LINES[0])),
        "reversed": " ".join(reversed(SMOKE_LINES)),
    }
    with pytest.raises(RuntimeError, match="exactly one occurrence|reading order"):
        smoke._check_text(invalid[bad_text])


@pytest.mark.parametrize("scenario", ["pass", "setup-failed", "one-engine", "receipt-mismatch"])
def test_acceptance_step_contract_and_failure_evidence(
    tmp_path: Path, monkeypatch, scenario
) -> None:
    evidence = tmp_path / "evidence"
    root = tmp_path / "workspace"
    signature = hashlib.sha256(b"scan").hexdigest()
    calls = []
    reviewed = False

    def image_creator(path):
        path.write_bytes(b"scan")
        return path

    def run(command, **kwargs):
        nonlocal reviewed
        subcommand = command[1]
        calls.append(command)
        assert "PYTHONPATH" not in kwargs["env"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        payload: dict[str, object] = {"schema_version": 1}
        if subcommand == "setup" and scenario == "setup-failed":
            kwargs["stderr"].write(b"network failure")
            return subprocess.CompletedProcess(command, 1)
        if subcommand == "runtime-status":
            payload["backends"] = {
                name: {"state": "ready", "environment_exists": True} for name in smoke.BACKENDS
            }
        elif subcommand == "status":
            payload["projects"] = [
                {
                    "pages_total": 1,
                    "pages_with_both_ocr_success": 0 if scenario == "one-engine" else 1,
                    "source_sha256": signature,
                }
            ]
        elif subcommand == "review":
            payload["projects"] = [
                {
                    "items": []
                    if reviewed
                    else [
                        {
                            "page_index": 0,
                            "pair_index": 0,
                            "review_token": "v1:r0",
                            "primary_text": SMOKE_LINES[0],
                            "secondary_text": "different",
                        }
                    ]
                }
            ]
        elif subcommand == "resolve":
            assert f"--text={SMOKE_LINES[0]}" in command
            assert "--review-token=v1:r0" in command
            reviewed = True
        elif subcommand == "export":
            assert reviewed
            Path(
                next(arg.removeprefix("--out=") for arg in command if arg.startswith("--out="))
            ).write_bytes(b"epub")
        elif subcommand == "verify-export":
            payload.update(
                status="mismatch" if scenario == "receipt-mismatch" else "consistent",
                project_source_matches=True,
            )
        kwargs["stdout"].write(json.dumps(payload).encode())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setattr(smoke, "read_epub_spine", lambda _: "\n".join(SMOKE_LINES))
    if scenario == "pass":
        result = smoke.acceptance(
            tmp_path / "scribeforge.exe", root, evidence, image_creator=image_creator
        )
        assert result["reviewed_pairs"] == 1
        assert (evidence / "passed.json").is_file()
        assert [call[1] for call in calls] == [
            "setup",
            "runtime-status",
            "process",
            "status",
            "review",
            "resolve",
            "review",
            "export",
            "verify-export",
        ]
    else:
        messages = {
            "setup-failed": "network failure",
            "one-engine": "both engines",
            "receipt-mismatch": "could not verify",
        }
        with pytest.raises(RuntimeError, match=messages[scenario]):
            smoke.acceptance(
                tmp_path / "scribeforge.exe", root, evidence, image_creator=image_creator
            )
        assert not (evidence / "passed.json").exists()
    assert (evidence / "01-setup.stderr").exists()
    assert calls[0][-1] == f"--runtime-root={root / 'runtime'}"
    with pytest.raises(FileExistsError):
        smoke.acceptance(tmp_path / "scribeforge.exe", root, evidence, image_creator=image_creator)
