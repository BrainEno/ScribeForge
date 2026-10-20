"""Entry point of the desktop shell.

The Tkinter layer is imported lazily so the CLI keeps working on Python builds
without Tkinter, and so importing the shell never requires a display.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO, cast

from scribeforge.storage.sqlite import SQLiteStore

EXIT_FATAL = 2

Launcher = Callable[[SQLiteStore, Path | None], int]


def run_ui(
    store: SQLiteStore,
    *,
    crops_dir: Path | None = None,
    stream: TextIO | None = None,
) -> int:
    """Open the read-only desktop shell for a project store."""

    error = sys.stderr if stream is None else stream
    try:
        return _launch_desktop_ui(store, crops_dir)
    except ImportError as exc:
        print(f"error: the desktop UI needs Tkinter ({exc})", file=error)
        return EXIT_FATAL


def _launch_desktop_ui(store: SQLiteStore, crops_dir: Path | None) -> int:
    module = importlib.import_module("scribeforge.ui.tk_window")
    launch = cast(Launcher, module.launch)
    return launch(store, crops_dir)
