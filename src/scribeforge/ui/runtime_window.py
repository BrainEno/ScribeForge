"""Standalone first-run wizard for application-managed OCR runtimes."""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from scribeforge.runtime.manager import RuntimeEvent
from scribeforge.runtime.models import HardwareProfile
from scribeforge.runtime.workflow import RuntimeOverview, RuntimeSetupWorkflow


class RuntimeSetupWindow:
    """Tkinter setup flow that performs downloads away from the UI thread."""

    def __init__(self, root: tk.Tk, runtime_root: Path) -> None:
        self._root = root
        self._runtime_root = runtime_root
        self._workflow = RuntimeSetupWorkflow(runtime_root)
        self._messages: queue.Queue[tuple[str, object]] = queue.Queue()
        self._busy = False
        self._last_error: str | None = None
        self._backend_items: dict[str, str] = {}

        root.title("ScribeForge — First-run setup")
        root.geometry("780x590")
        root.minsize(680, 500)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        main = ttk.Frame(root, padding=14)
        main.pack(fill="both", expand=True)

        ttk.Label(
            main,
            text="Prepare local OCR runtimes",
            font=("TkDefaultFont", 15, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            main,
            text=(
                "ScribeForge installs its own Python environments and OCR models. "
                "Your global Python and book files are not modified."
            ),
            wraplength=730,
        ).pack(anchor="w", pady=(4, 10))

        self._hardware_label = ttk.Label(
            main, text="Detecting hardware…", wraplength=730, justify="left"
        )
        self._hardware_label.pack(anchor="w", pady=(0, 4))
        self._bootstrap_label = ttk.Label(
            main, text="Bootstrap tool: checking", wraplength=730
        )
        self._bootstrap_label.pack(anchor="w", pady=(0, 8))
        ttk.Label(main, text=f"Private runtime folder: {runtime_root}", wraplength=730).pack(
            anchor="w", pady=(0, 8)
        )

        self._backend_table = ttk.Treeview(
            main,
            columns=("state", "failure"),
            show="tree headings",
            height=2,
        )
        self._backend_table.heading("#0", text="Backend")
        self._backend_table.column("#0", width=150, anchor="w")
        self._backend_table.heading("state", text="Status")
        self._backend_table.column("state", width=130, anchor="w")
        self._backend_table.heading("failure", text="Failed step")
        self._backend_table.column("failure", width=350, anchor="w")
        self._backend_table.pack(fill="x", pady=(0, 8))
        for backend in ("mineru", "paddleocr"):
            item = self._backend_table.insert(
                "", "end", text=backend, values=("checking", "—")
            )
            self._backend_items[backend] = item

        self._progress = ttk.Progressbar(main, mode="indeterminate")
        self._progress.pack(fill="x", pady=(0, 8))
        self._status = ttk.Label(main, text="Checking current runtime state…", wraplength=730)
        self._status.pack(anchor="w", pady=(0, 6))

        log_frame = ttk.LabelFrame(main, text="Setup activity")
        log_frame.pack(fill="both", expand=True, pady=(0, 10))
        self._log = tk.Text(log_frame, height=9, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self._log.yview)
        self._log.configure(yscrollcommand=log_scroll.set)
        self._log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        actions = ttk.Frame(main)
        actions.pack(fill="x")
        self._install_button = ttk.Button(
            actions, text="Install / repair all", command=lambda: self._start_task("install")
        )
        self._install_button.pack(side="left", padx=(0, 6))
        self._mineru_button = ttk.Button(
            actions,
            text="Repair MinerU",
            command=lambda: self._start_task("repair", "mineru"),
        )
        self._mineru_button.pack(side="left", padx=(0, 6))
        self._paddle_button = ttk.Button(
            actions,
            text="Repair PaddleOCR",
            command=lambda: self._start_task("repair", "paddleocr"),
        )
        self._paddle_button.pack(side="left", padx=(0, 6))
        self._refresh_button = ttk.Button(
            actions, text="Refresh status", command=lambda: self._start_task("inspect")
        )
        self._refresh_button.pack(side="left")
        self._close_button = ttk.Button(actions, text="Close", command=self._on_close)
        self._close_button.pack(side="right")

        self._buttons = (
            self._install_button,
            self._mineru_button,
            self._paddle_button,
            self._refresh_button,
            self._close_button,
        )
        self._start_task("inspect")
        root.after(100, self._drain_messages)

    def _start_task(self, action: str, backend: str | None = None) -> None:
        if self._busy:
            return
        self._busy = True
        self._last_error = None
        self._set_controls_enabled(False)
        self._progress.start(12)
        if action == "install":
            self._status.configure(text="Installing private OCR runtimes and preparing models…")
            self._append_log("Starting setup for MinerU and PaddleOCR.")
        elif action == "repair":
            self._status.configure(text=f"Repairing {backend}…")
            self._append_log(f"Starting repair for {backend}.")
        else:
            self._status.configure(text="Checking runtime and hardware status…")
        threading.Thread(
            target=self._run_task,
            args=(action, backend),
            name="scribeforge-runtime-setup",
            daemon=True,
        ).start()

    def _run_task(self, action: str, backend: str | None) -> None:
        try:
            if action == "install":
                self._workflow.install(on_event=self._queue_event)
            elif action == "repair" and backend is not None:
                self._workflow.repair(backend, on_event=self._queue_event)
            overview = self._workflow.inspect()
            self._messages.put(("overview", overview))
        except Exception as exc:  # noqa: BLE001 - surface setup failures in the wizard
            self._messages.put(("error", str(exc)))
            try:
                self._messages.put(("overview", self._workflow.inspect()))
            except Exception as inspect_error:  # noqa: BLE001
                self._messages.put(("error", f"Could not refresh runtime state: {inspect_error}"))
        finally:
            self._messages.put(("done", None))

    def _queue_event(self, event: RuntimeEvent) -> None:
        self._messages.put(("event", event))

    def _drain_messages(self) -> None:
        while True:
            try:
                kind, payload = self._messages.get_nowait()
            except queue.Empty:
                break
            if kind == "overview" and isinstance(payload, RuntimeOverview):
                self._show_overview(payload)
            elif kind == "event" and isinstance(payload, RuntimeEvent):
                self._show_event(payload)
            elif kind == "error":
                self._last_error = str(payload)
                self._status.configure(text=f"Setup failed: {payload}")
                self._append_log(f"ERROR: {payload}")
            elif kind == "done":
                self._busy = False
                self._progress.stop()
                self._set_controls_enabled(True)
                if self._last_error is None:
                    self._status.configure(text="Runtime status updated.")
                else:
                    self._status.configure(text="Setup needs attention. See the activity log.")
        self._root.after(100, self._drain_messages)

    def _show_overview(self, overview: RuntimeOverview) -> None:
        profile = overview.hardware
        self._hardware_label.configure(text=_hardware_summary(profile))
        self._bootstrap_label.configure(
            text=(
                "Bootstrap tool: available"
                if overview.uv_available
                else "Bootstrap tool: not present yet; setup will download and verify it"
            )
        )
        for backend, status in (
            ("mineru", overview.mineru),
            ("paddleocr", overview.paddleocr),
        ):
            failure = status.failed_step or "—"
            self._backend_table.item(
                self._backend_items[backend], values=(status.state, failure)
            )
        if overview.mineru.state == "ready" and overview.paddleocr.state == "ready":
            self._append_log("Both OCR backends are ready.")
        elif overview.mineru.state == "failed" or overview.paddleocr.state == "failed":
            self._append_log("At least one backend needs repair; repair actions are independent.")

    def _show_event(self, event: RuntimeEvent) -> None:
        step = f", step {event.step}" if event.step else ""
        progress = ""
        if event.total_steps:
            progress = f" ({event.completed_steps}/{event.total_steps} steps)"
        self._append_log(
            f"{event.backend}: {event.phase} — {event.state}{step}{progress}"
        )
        if event.state == "failed":
            self._status.configure(
                text=f"{event.backend} failed at {event.phase}:{event.step or 'unknown'}"
            )

    def _append_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line + "\n")
        line_count = int(self._log.index("end-1c").split(".")[0])
        if line_count > 400:
            self._log.delete("1.0", "101.0")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._buttons:
            button.configure(state=state)

    def _on_close(self) -> None:
        if self._busy:
            messagebox.showwarning(
                "Setup in progress",
                "Wait for the current setup or repair operation to finish before closing.",
                parent=self._root,
            )
            return
        self._root.destroy()


def _hardware_summary(profile: HardwareProfile) -> str:
    fields = [f"System: {profile.os_name} / {profile.architecture}"]
    if profile.gpu_name:
        fields.append(f"GPU: {profile.gpu_name}")
    if profile.nvidia_driver:
        fields.append(f"NVIDIA driver: {profile.nvidia_driver}")
    if profile.vram_mb is not None:
        fields.append(f"VRAM: {profile.vram_mb / 1024:.1f} GiB")
    if profile.ram_mb is not None:
        fields.append(f"RAM: {profile.ram_mb / 1024:.1f} GiB")
    if profile.gpu_name is None:
        fields.append("No NVIDIA GPU detected; PaddleOCR will use CPU mode")
    return "  ·  ".join(fields)


def run_runtime_setup(runtime_root: Path) -> int:
    """Launch the standalone setup wizard, without requiring a project store."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"error: cannot open the runtime setup window: {exc}", file=sys.stderr)
        return 2
    RuntimeSetupWindow(root, runtime_root)
    root.mainloop()
    return 0
