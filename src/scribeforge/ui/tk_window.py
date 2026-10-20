"""Tkinter view of the desktop shell.

Display-only code: every label and every row comes from
:mod:`scribeforge.ui.viewmodel`, which is unit tested without a display. This
module is excluded from coverage and verified by launching the app.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import ImageTk

from scribeforge.adapters.image_preview import load_preview
from scribeforge.storage.sqlite import SQLiteStore
from scribeforge.ui.viewmodel import (
    ReviewRow,
    load_jobs,
    load_library,
    load_pair_view,
    load_review_rows,
    render_detail,
)


class ScribeForgeWindow:
    """Read-only shell: library, job progress, review queue, pair evidence."""

    def __init__(
        self, root: tk.Tk, store: SQLiteStore, *, crops_dir: Path | None = None
    ) -> None:
        self._root = root
        self._store = store
        self._crops_dir = crops_dir
        self._projects: dict[str, int] = {}
        self._queue: dict[str, ReviewRow] = {}
        self._project_id: int | None = None
        self._photos: dict[ttk.Label, ImageTk.PhotoImage] = {}

        root.title("ScribeForge")
        root.geometry("1180x680")

        panes = ttk.PanedWindow(root, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=8)

        library_frame = ttk.Frame(panes)
        ttk.Label(library_frame, text="Library").pack(anchor="w")
        self.library = ttk.Treeview(
            library_frame,
            columns=("pages", "queued", "failed"),
            show="tree headings",
            selectmode="browse",
        )
        self.library.heading("#0", text="Project")
        self.library.column("#0", width=240, anchor="w")
        for column, heading in (("pages", "Pages"), ("queued", "Queued"), ("failed", "Failed")):
            self.library.heading(column, text=heading)
            self.library.column(column, width=70, anchor="w")
        self.library.pack(fill="both", expand=True)
        ttk.Label(library_frame, text="Jobs").pack(anchor="w")
        self.jobs = tk.Listbox(library_frame, height=8)
        self.jobs.pack(fill="both", expand=False)
        panes.add(library_frame, weight=1)

        review_frame = ttk.Frame(panes)
        ttk.Label(review_frame, text="Review queue").pack(anchor="w")
        self.review = ttk.Treeview(
            review_frame,
            columns=("pair", "score", "reasons"),
            show="tree headings",
            selectmode="browse",
        )
        self.review.heading("#0", text="Page")
        self.review.column("#0", width=70, anchor="w")
        for column, heading in (("pair", "Pair"), ("score", "Score"), ("reasons", "Reasons")):
            self.review.heading(column, text=heading)
            self.review.column(column, width=90, anchor="w")
        self.review.pack(fill="both", expand=True)
        panes.add(review_frame, weight=1)

        detail_frame = ttk.Frame(panes)
        ttk.Label(detail_frame, text="Pair view").pack(anchor="w")
        notebook = ttk.Notebook(detail_frame)
        notebook.pack(fill="both", expand=True)

        evidence_tab = ttk.Frame(notebook)
        self.detail = tk.Text(evidence_tab, wrap="word", width=48, state="disabled")
        self.detail.pack(fill="both", expand=True)
        notebook.add(evidence_tab, text="Evidence")

        page_tab = ttk.Frame(notebook)
        self.page_caption = ttk.Label(page_tab, text="no page image", anchor="w")
        self.page_caption.pack(fill="x")
        self.page_image = ttk.Label(page_tab)
        self.page_image.pack(fill="both", expand=True)
        notebook.add(page_tab, text="Page image")

        crop_tab = ttk.Frame(notebook)
        self.crop_caption = ttk.Label(crop_tab, text="no crop", anchor="w")
        self.crop_caption.pack(fill="x")
        self.crop_image = ttk.Label(crop_tab)
        self.crop_image.pack(fill="both", expand=True)
        notebook.add(crop_tab, text="Crop")
        panes.add(detail_frame, weight=2)

        self.status = ttk.Label(root, text="", anchor="w")
        self.status.pack(fill="x", padx=8, pady=(0, 8))

        self.library.bind("<<TreeviewSelect>>", self._on_project_selected)
        self.review.bind("<<TreeviewSelect>>", self._on_pair_selected)
        root.bind("r", lambda event: self.refresh())
        root.bind("<F5>", lambda event: self.refresh())

    def refresh(self) -> None:
        """Reload the library from the store and show the first project."""

        self.library.delete(*self.library.get_children())
        self._projects.clear()
        entries = load_library(self._store)
        for entry in entries:
            item = self.library.insert(
                "",
                "end",
                text=entry.title,
                values=(entry.page_count, entry.review_queued, entry.failed_jobs),
            )
            self._projects[item] = entry.project_id
        if not entries:
            self.jobs.delete(0, "end")
            self.review.delete(*self.review.get_children())
            self.status.configure(text="no projects in this store")
            self._clear_images("no page image", "no crop")
            self._set_detail("no projects in this store")
            return
        first = self.library.get_children()[0]
        self.library.selection_set(first)
        self.library.focus(first)
        self._show_project(self._projects[first])

    def _show_project(self, project_id: int) -> None:
        self._project_id = project_id
        self.jobs.delete(0, "end")
        for job in load_jobs(self._store, project_id):
            self.jobs.insert("end", job.label)
        self.review.delete(*self.review.get_children())
        self._queue.clear()
        rows = load_review_rows(self._store, project_id)
        for queued in rows:
            item = self.review.insert(
                "",
                "end",
                text=f"page {queued.page_index}",
                values=(
                    queued.pair_index,
                    f"{queued.score:.2f}",
                    ",".join(queued.reasons) or "none",
                ),
            )
            self._queue[item] = queued
        self.status.configure(text=f"{len(rows)} pair(s) queued for review")
        if not rows:
            self._clear_images("no page image for this page", "no crop for this pair")
            self._set_detail("nothing queued for review")
            return
        first = self.review.get_children()[0]
        self.review.selection_set(first)
        self.review.focus(first)
        self._show_pair(self._queue[first])

    def _on_project_selected(self, event: object) -> None:
        selection = self.library.selection()
        if selection:
            self._show_project(self._projects[selection[0]])

    def _on_pair_selected(self, event: object) -> None:
        selection = self.review.selection()
        if selection:
            self._show_pair(self._queue[selection[0]])

    def _show_pair(self, row: ReviewRow) -> None:
        if self._project_id is None:
            return
        view = load_pair_view(
            self._store, self._project_id, row, crops_dir=self._crops_dir
        )
        self._set_detail(render_detail(view.detail))
        self._show_image(
            self.page_image,
            self.page_caption,
            view.page_image,
            box=(560, 720),
            upscale=False,
            missing="no page image for this page",
        )
        self._show_image(
            self.crop_image,
            self.crop_caption,
            view.crop_image,
            box=(560, 240),
            upscale=True,
            missing="no crop for this pair",
        )
        self.status.configure(
            text=f"page {row.page_index} pair {row.pair_index} — {view.sources_label}"
        )

    def _show_image(
        self,
        holder: ttk.Label,
        caption: ttk.Label,
        path: Path | None,
        *,
        box: tuple[int, int],
        upscale: bool,
        missing: str,
    ) -> None:
        holder.configure(image="")
        self._photos.pop(holder, None)
        if path is None:
            caption.configure(text=missing)
            return
        try:
            preview = load_preview(path, box=box, upscale=upscale)
        except (OSError, ValueError) as exc:
            caption.configure(text=f"cannot read {path.name}: {exc}")
            return
        photo = ImageTk.PhotoImage(preview)
        self._photos[holder] = photo
        holder.configure(image=photo)
        caption.configure(text=f"{path}  ({preview.width}×{preview.height})")

    def _clear_images(self, page_note: str, crop_note: str) -> None:
        self._show_image(
            self.page_image,
            self.page_caption,
            None,
            box=(560, 720),
            upscale=False,
            missing=page_note,
        )
        self._show_image(
            self.crop_image,
            self.crop_caption,
            None,
            box=(560, 240),
            upscale=True,
            missing=crop_note,
        )

    def _set_detail(self, text: str) -> None:
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")


def launch(store: SQLiteStore, crops_dir: Path | None = None) -> int:
    """Open the window and run the Tk event loop until it closes."""

    root = tk.Tk()
    window = ScribeForgeWindow(root, store, crops_dir=crops_dir)
    window.refresh()
    root.mainloop()
    return 0
