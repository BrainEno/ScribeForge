"""Surface a concise pytest failure as a GitHub Actions check annotation."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape_command_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    report = Path(sys.argv[1])
    if not report.is_file():
        print(f"::error title=Python compatibility tests::{report} was not generated")
        return 0

    root = ET.parse(report).getroot()
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            issue = case.find(kind)
            if issue is None:
                continue
            identity = "::".join(
                part for part in (case.get("classname", ""), case.get("name", "")) if part
            )
            details = " ".join(
                part for part in (issue.get("message", ""), issue.text or "") if part
            )
            message = f"{identity}: {details}" if details else identity
            print(
                "::error title=Python compatibility test failure::"
                f"{_escape_command_data(message[:3000])}"
            )
            return 0

    print(f"::error title=Python compatibility tests::{report} contains no failure details")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
