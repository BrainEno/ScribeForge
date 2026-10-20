from __future__ import annotations

import types
from pathlib import Path

import pytest

from scribeforge.storage.sqlite import SQLiteStore
from scribeforge.ui.app import run_ui


def _fake_module() -> types.SimpleNamespace:
    def launch(store: SQLiteStore, crops_dir: Path | None) -> int:
        return 0

    return types.SimpleNamespace(launch=launch)


def test_run_ui_reports_a_missing_tkinter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(store: SQLiteStore, crops_dir: Path | None) -> int:
        raise ImportError("no tkinter")

    monkeypatch.setattr("scribeforge.ui.app._launch_desktop_ui", explode)
    store = SQLiteStore(tmp_path / "project.sqlite3")

    code = run_ui(store)

    captured = capsys.readouterr()
    assert code == 2
    assert "tkinter" in captured.err.lower()


def test_run_ui_hands_the_store_and_crops_to_the_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[SQLiteStore, Path | None]] = []

    def fake(store: SQLiteStore, crops_dir: Path | None) -> int:
        seen.append((store, crops_dir))
        return 0

    monkeypatch.setattr("scribeforge.ui.app._launch_desktop_ui", fake)
    store = SQLiteStore(tmp_path / "project.sqlite3")

    code = run_ui(store, crops_dir=tmp_path / "crops")

    assert code == 0
    assert seen == [(store, tmp_path / "crops")]


def test_launch_resolves_the_tkinter_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requested: list[str] = []
    monkeypatch.setattr(
        "scribeforge.ui.app.importlib.import_module",
        lambda name: requested.append(name) or _fake_module(),
    )
    store = SQLiteStore(tmp_path / "project.sqlite3")

    code = run_ui(store)

    assert code == 0
    assert requested == ["scribeforge.ui.tk_window"]
