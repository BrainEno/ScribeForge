from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected a JSON object in {path}")
    return value


def _code_block(text: str) -> str:
    return f"```text\n{text.replace('```', '` ` `')}\n```"


def build_summary(evidence_dir: Path, status: str) -> str:
    lines = [
        "<!-- scribeforge-paddle-real-smoke -->",
        f"## Real Paddle smoke — status: {status}",
        "",
    ]
    model_path = evidence_dir / "model-evidence.json"
    worker_path = evidence_dir / "worker-result.json"

    if model_path.is_file():
        evidence = _load_json(model_path)
        distributions = evidence.get("installed_distributions", {})
        lines.extend(
            [
                "### Managed runtime",
                "",
                f"- Lock SHA-256: `{evidence.get('lock_file', {}).get('sha256', 'missing')}`",
            ]
        )
        for name, version in sorted(distributions.items()):
            lines.append(f"- {name}: `{version}`")
        lines.extend(["", "### Exact model revisions", ""])
        for model in evidence.get("models", []):
            lines.append(f"- {model.get('name')}: `{model.get('revision')}`")
        lines.append("")

    if worker_path.is_file():
        document = _load_json(worker_path)
        worker_lines = document.get("lines", [])
        tokens = sum(len(line.get("tokens", [])) for line in worker_lines if isinstance(line, dict))
        lines.extend(
            [
                "### Real worker result",
                "",
                f"- Schema: `{document.get('schema_version')}`",
                f"- Lines: **{len(worker_lines)}**",
                f"- Native geometry: **{tokens} native word boxes**",
            ]
        )
        if status == "success":
            lines.append("- **Real PP-OCRv4 smoke passed.**")
        lines.append("")

    if status != "success":
        setup_path = evidence_dir / "setup.jsonl"
        runner_path = evidence_dir / "runner.log"
        log_path = runner_path if runner_path.is_file() else setup_path
        if log_path.is_file():
            log_tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            lines.extend(["### Runner log tail", "", _code_block("\n".join(log_tail[-80:])), ""])
        junit_path = evidence_dir / "junit.xml"
        if junit_path.is_file():
            root = ET.parse(junit_path).getroot()
            failures = [
                element.text or "" for element in root.iter() if element.tag in {"failure", "error"}
            ]
            if failures:
                lines.extend(
                    ["### Smoke failure", "", _code_block("\n\n".join(failures)[-12000:]), ""]
                )
        if not log_path.is_file() and not junit_path.is_file():
            lines.extend(
                [
                    "No setup log or JUnit file was produced; inspect the job step log.",
                    "",
                ]
            )

    lines.extend(
        [
            "**Recognition accuracy was not evaluated.** This run only checks installation, exact model bytes, native word geometry, and the worker protocol.",
            "",
            "Full logs and JSON are attached to this run's `paddle-real-smoke` artifact.",
        ]
    )
    summary = "\n".join(lines)
    return summary[:55000]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_dir", type=Path)
    parser.add_argument("status")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(
        build_summary(args.evidence_dir, args.status) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
