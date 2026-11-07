from __future__ import annotations

import sys
from types import SimpleNamespace

from scribeforge.workers import protocol


def test_engine_output_is_forwarded_to_bounded_parent_stderr(monkeypatch) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(protocol.subprocess, "run", fake_run)

    protocol.run_engine_command(("engine", "--input", "page.png"))

    assert calls == [
        (
            ("engine", "--input", "page.png"),
            {"check": True, "stdout": sys.stderr, "stderr": sys.stderr},
        )
    ]
