"""Tkinter widgets for the desktop review shell.

Evidence presentation and decision persistence are separated into the
framework-independent view-model. This widget module is excluded from coverage
because CI is headless; its underlying decision behavior is unit tested.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, cast

from PIL import ImageDraw, ImageTk

from scribeforge.adapters.image_preview import load_preview
from scribeforge.domain.ocr import BoundingBox
from scribeforge.pipeline.adjudication import ReviewDetail
from scribeforge.storage.sqlite import SQLiteStore
from scribeforge.ui.viewmodel import (
    DiffPresentation,
    ReviewRow,
    load_jobs,
    load_library,
    load_pair_view,
    load_review_rows,
    render_conflict_side,
    render_detail,
    resolve_review_row,
)


class ScribeForgeWindow:
    """Keyboard-first review shell over immutable OCR evidence."""

    def __init__(
        self, root: tk.Tk, store: SQLiteStore, *, crops_dir: Path | None = None
    ) -> None:
        self._root = root
        self._store = store
        self._crops_dir = crops_dir
        self._projects: dict[str, int] = {}
        self._queue: dict[str, ReviewRow] = {}
        self._project_id: int | None = None
        self._current_row: ReviewRow | None = None
        self._current_detail: ReviewDetail | None = None
        self._current_page_image: Path | None = None
        self._photos: dict[ttk.Label, ImageTk.PhotoImage] = {}
        self._selected_conflict_index: int | None = None
        self._comparison_presentations: dict[str, DiffPresentation] = {}

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
        decision_frame = ttk.LabelFrame(detail_frame, text="Human decision")
        decision_frame.pack(fill="x", pady=(4, 4))
        choice_row = ttk.Frame(decision_frame)
        choice_row.pack(fill="x", padx=4, pady=(4, 2))
        self.primary_button = ttk.Button(
            choice_row, text="Use primary [1]",
            command=lambda: self._accept_reading("primary"),
        )
        self.primary_button.pack(side="left", padx=(0, 4))
        self.secondary_button = ttk.Button(
            choice_row, text="Use secondary [2]",
            command=lambda: self._accept_reading("secondary"),
        )
        self.secondary_button.pack(side="left", padx=(0, 4))
        self.vlm_button = ttk.Button(
            choice_row, text="Use VLM [3]",
            command=lambda: self._accept_reading("vlm"),
        )
        self.vlm_button.pack(side="left", padx=(0, 4))
        self.next_button = ttk.Button(
            choice_row, text="Next pair [n]", command=self._select_next_pair,
        )
        self.next_button.pack(side="left")
        focus_row = ttk.Frame(decision_frame)
        focus_row.pack(fill="x", padx=4, pady=(2, 2))
        ttk.Label(focus_row, text="Source focus:").pack(side="left", padx=(0, 4))
        self.focus_pair_button = ttk.Button(
            focus_row,
            text="Pair region [p]",
            command=lambda: self._focus_source("pair"),
        )
        self.focus_pair_button.pack(side="left", padx=(0, 4))
        self.focus_primary_button = ttk.Button(
            focus_row,
            text="Primary line [a]",
            command=lambda: self._focus_source("primary"),
        )
        self.focus_primary_button.pack(side="left", padx=(0, 4))
        self.focus_secondary_button = ttk.Button(
            focus_row,
            text="Secondary line [d]",
            command=lambda: self._focus_source("secondary"),
        )
        self.focus_secondary_button.pack(side="left")

        edit_row = ttk.Frame(decision_frame)
        edit_row.pack(fill="x", padx=4, pady=(2, 4))
        ttk.Label(edit_row, text="Literal text:").pack(side="left", padx=(0, 4))
        self.custom_entry = ttk.Entry(edit_row)
        self.custom_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.save_button = ttk.Button(
            edit_row, text="Save [Ctrl+Enter]", command=self._save_custom,
        )
        self.save_button.pack(side="right")

        notebook = ttk.Notebook(detail_frame)
        self._notebook = notebook
        notebook.pack(fill="both", expand=True)

        evidence_tab = ttk.Frame(notebook)
        self.detail = tk.Text(evidence_tab, wrap="word", width=48, state="disabled")
        self.detail.pack(fill="both", expand=True)
        notebook.add(evidence_tab, text="Evidence")

        compare_tab = ttk.Frame(notebook)
        self.compare_summary = ttk.Label(
            compare_tab, text="Character-level differences from stored alignment"
        )
        self.compare_summary.pack(anchor="w", padx=4, pady=(4, 2))
        compare_panes = ttk.PanedWindow(compare_tab, orient="horizontal")
        compare_panes.pack(fill="both", expand=True, padx=4, pady=4)
        primary_frame = ttk.LabelFrame(compare_panes, text="Primary OCR")
        secondary_frame = ttk.LabelFrame(compare_panes, text="Secondary OCR")
        primary_text_frame = ttk.Frame(primary_frame)
        secondary_text_frame = ttk.Frame(secondary_frame)
        self.primary_compare = tk.Text(
            primary_text_frame, wrap="word", width=24, height=12, state="disabled"
        )
        self.secondary_compare = tk.Text(
            secondary_text_frame, wrap="word", width=24, height=12, state="disabled"
        )
        primary_scroll = ttk.Scrollbar(
            primary_text_frame, orient="vertical", command=self.primary_compare.yview
        )
        secondary_scroll = ttk.Scrollbar(
            secondary_text_frame, orient="vertical", command=self.secondary_compare.yview
        )
        self.primary_compare.configure(yscrollcommand=primary_scroll.set)
        self.secondary_compare.configure(yscrollcommand=secondary_scroll.set)
        self.primary_compare.pack(side="left", fill="both", expand=True)
        primary_scroll.pack(side="right", fill="y")
        self.secondary_compare.pack(side="left", fill="both", expand=True)
        secondary_scroll.pack(side="right", fill="y")
        primary_text_frame.pack(fill="both", expand=True)
        secondary_text_frame.pack(fill="both", expand=True)
        compare_panes.add(primary_frame, weight=1)
        compare_panes.add(secondary_frame, weight=1)
        for widget in (self.primary_compare, self.secondary_compare):
            widget.tag_configure("substitution", background="#fee2e2")
            widget.tag_configure("insertion", background="#dcfce7")
            widget.tag_configure("deletion", background="#ffedd5")
            widget.tag_configure("punctuation", background="#dbeafe")
            widget.tag_configure(
                "selected_conflict", background="#fde047", foreground="#111827", underline=True
            )
        ttk.Label(
            compare_tab,
            text="Red: replacement   Green: insertion   Orange: deletion   Blue: punctuation",
        ).pack(anchor="w", padx=4, pady=(0, 4))
        notebook.add(compare_tab, text="Compare")

        page_tab = ttk.Frame(notebook)
        self._page_tab = page_tab
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
        root.bind("r", lambda event: self._shortcut_refresh())
        root.bind("<F5>", lambda event: self.refresh())
        root.bind("1", lambda event: self._shortcut_accept("primary"))
        root.bind("2", lambda event: self._shortcut_accept("secondary"))
        root.bind("3", lambda event: self._shortcut_accept("vlm"))
        root.bind("n", lambda event: self._select_next_pair())
        root.bind("p", lambda event: self._shortcut_focus("pair"))
        root.bind("a", lambda event: self._shortcut_focus("primary"))
        root.bind("d", lambda event: self._shortcut_focus("secondary"))
        root.bind("<Control-Return>", lambda event: self._shortcut_save())

    def refresh(self) -> None:
        """Reload the library, preserving the currently selected project."""
        preferred_project_id = self._project_id
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
            self._queue.clear()
            self._clear_current_review()
            self.status.configure(text="no projects in this store")
            self._clear_images("no page image", "no crop")
            self._set_detail("no projects in this store")
            return
        children = self.library.get_children()
        selected = next(
            (item for item in children if self._projects[item] == preferred_project_id),
            children[0],
        )
        self.library.selection_set(selected)
        self.library.focus(selected)
        self._show_project(self._projects[selected])

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
            self._clear_current_review()
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
        self._current_row = row
        self._current_detail = view.detail
        self._current_page_image = view.page_image
        self._set_choice_states(view.detail)
        self.custom_entry.delete(0, "end")
        self._set_detail(render_detail(view.detail))
        self._render_comparison(view.detail)
        self._show_image(
            self.page_image,
            self.page_caption,
            view.page_image,
            box=(560, 720),
            upscale=False,
            missing="no page image for this page",
            focus_box=view.detail.source_box,
            focus_color="#f59e0b",
            focus_label="pair region",
        )
        self._show_image(
            self.crop_image,
            self.crop_caption,
            view.crop_image,
            box=(560, 240),
            upscale=True,
            missing="no crop for this pair",
        )
        self._select_page_tab()
        self.status.configure(
            text=(
                f"page {row.page_index} pair {row.pair_index} — "
                f"{view.sources_label}; source region highlighted"
            )
        )

    def _set_choice_states(self, detail: ReviewDetail | None) -> None:
        """Only enable evidence shortcuts when that source contains a reading."""
        self.primary_button.configure(
            state="normal" if detail is not None and detail.primary_text is not None else "disabled"
        )
        self.secondary_button.configure(
            state=(
                "normal"
                if detail is not None and detail.secondary_text is not None
                else "disabled"
            )
        )
        self.vlm_button.configure(
            state="normal" if detail is not None and detail.vlm_text is not None else "disabled"
        )
        self.focus_pair_button.configure(
            state="normal" if detail is not None and detail.source_box is not None else "disabled"
        )
        self.focus_primary_button.configure(
            state="normal" if detail is not None and detail.primary_box is not None else "disabled"
        )
        self.focus_secondary_button.configure(
            state=(
                "normal"
                if detail is not None and detail.secondary_box is not None
                else "disabled"
            )
        )

    def _clear_comparison(self) -> None:
        """Avoid showing stale OCR from the last item when the queue is empty."""
        self._selected_conflict_index = None
        self._comparison_presentations.clear()
        empty = DiffPresentation("No pair selected", ())
        self._set_comparison_text(self.primary_compare, empty)
        self._set_comparison_text(self.secondary_compare, empty)
        self.compare_summary.configure(text="No character-level comparison selected")

    def _clear_current_review(self) -> None:
        self._current_row = None
        self._current_detail = None
        self._set_choice_states(None)
        self.custom_entry.delete(0, "end")
        self._clear_comparison()

    def _accept_reading(self, source: str) -> None:
        """Accept one explicitly selected reading as a human decision."""
        detail = self._current_detail
        if detail is None:
            return
        readings = {
            "primary": detail.primary_text,
            "secondary": detail.secondary_text,
            "vlm": detail.vlm_text,
        }
        selected = readings.get(source)
        if selected is None:
            return
        self._commit_decision(selected)

    def _save_custom(self) -> None:
        """Save the literal text from the editor, including intentional emptiness."""
        if self._current_row is None or self._project_id is None:
            return
        selected = self.custom_entry.get()
        if selected == "" and not messagebox.askyesno(
            "Accept empty transcription?",
            "This records an empty reading for this pair. Continue?",
            parent=self._root,
        ):
            return
        self._commit_decision(selected)

    def _commit_decision(self, selected_text: str) -> None:
        row = self._current_row
        project_id = self._project_id
        if row is None or project_id is None:
            return
        try:
            resolve_review_row(self._store, project_id, row, selected_text)
        except Exception as exc:  # noqa: BLE001 - surface storage/stale-row errors in the UI
            messagebox.showerror("Could not record decision", str(exc), parent=self._root)
            self.refresh()
            return
        self.refresh()
        remaining = len(load_review_rows(self._store, project_id))
        self.status.configure(
            text=f"accepted page {row.page_index} pair {row.pair_index}; "
            f"{remaining} pair(s) remain"
        )

    def _shortcut_save(self) -> str:
        self._save_custom()
        return "break"

    def _shortcut_accept(self, source: str) -> str | None:
        if self._root.focus_get() is self.custom_entry:
            return None
        self._accept_reading(source)
        return "break"

    def _shortcut_refresh(self) -> str | None:
        if self._root.focus_get() is self.custom_entry:
            return None
        self.refresh()
        return "break"

    def _select_next_pair(self) -> str | None:
        """Move to the next queue item; don't hijack text typed in the editor."""
        if self._root.focus_get() is self.custom_entry:
            return None
        children = list(self.review.get_children())
        if not children:
            return "break"
        selection = self.review.selection()
        if not selection:
            next_item = children[0]
        else:
            current_index = children.index(selection[0])
            if current_index + 1 >= len(children):
                self.status.configure(text="already at the last queued pair")
                return "break"
            next_item = children[current_index + 1]
        self.review.selection_set(next_item)
        self.review.focus(next_item)
        self.review.see(next_item)
        return "break"

    def _select_page_tab(self) -> None:
        """Select the source tab; tkinter's dynamic select method is untyped."""
        cast(Any, self._notebook).select(self._page_tab)

    def _focus_source(self, target: str, *, status_text: str | None = None) -> None:
        """Show the source page with the selected OCR geometry overlaid."""
        detail = self._current_detail
        if detail is None:
            return
        boxes = {
            "pair": (detail.source_box, "#f59e0b", "pair region"),
            "primary": (detail.primary_box, "#2563eb", "primary OCR line"),
            "secondary": (detail.secondary_box, "#16a34a", "secondary OCR line"),
        }
        focus = boxes.get(target)
        if focus is None:
            return
        box, color, label = focus
        if box is None:
            if status_text is not None:
                self.status.configure(text=f"{status_text} — no source geometry for this side")
            return
        self._show_image(
            self.page_image,
            self.page_caption,
            self._current_page_image,
            box=(560, 720),
            upscale=False,
            missing="no page image for this page",
            focus_box=box,
            focus_color=color,
            focus_label=label,
        )
        self._select_page_tab()
        self.status.configure(text=status_text or f"source focus: {label}")

    def _shortcut_focus(self, target: str) -> str | None:
        if self._root.focus_get() is self.custom_entry:
            return None
        self._focus_source(target)
        return "break"

    def _show_image(
        self,
        holder: ttk.Label,
        caption: ttk.Label,
        path: Path | None,
        *,
        box: tuple[int, int],
        upscale: bool,
        missing: str,
        focus_box: BoundingBox | None = None,
        focus_color: str = "#f59e0b",
        focus_label: str | None = None,
    ) -> None:
        holder.configure(image="")
        self._photos.pop(holder, None)
        if path is None:
            caption.configure(text=missing)
            return
        try:
            preview = load_preview(path, box=box, upscale=upscale)
            if focus_box is not None:
                left = max(0, min(preview.width - 1, round(focus_box.x * preview.width)))
                top = max(0, min(preview.height - 1, round(focus_box.y * preview.height)))
                right = max(
                    left,
                    min(
                        preview.width - 1,
                        round((focus_box.x + focus_box.width) * preview.width),
                    ),
                )
                bottom = max(
                    top,
                    min(
                        preview.height - 1,
                        round((focus_box.y + focus_box.height) * preview.height),
                    ),
                )
                ImageDraw.Draw(preview).rectangle(
                    (left, top, right, bottom),
                    outline=focus_color,
                    width=max(2, min(preview.size) // 240),
                )
        except (OSError, ValueError) as exc:
            caption.configure(text=f"cannot read {path.name}: {exc}")
            return
        photo = ImageTk.PhotoImage(preview)
        self._photos[holder] = photo
        holder.configure(image=photo)
        focus_note = "" if focus_label is None or focus_box is None else f" — {focus_label}"
        caption.configure(
            text=f"{path}  ({preview.width}×{preview.height}){focus_note}"
        )

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

    def _render_comparison(self, detail: ReviewDetail) -> None:
        """Render stored alignment offsets in two read-only, side-by-side panes."""
        primary = render_conflict_side(
            detail.primary_text, detail.conflicts, primary=True
        )
        secondary = render_conflict_side(
            detail.secondary_text, detail.conflicts, primary=False
        )
        self._selected_conflict_index = None
        self._comparison_presentations = {
            "primary": primary,
            "secondary": secondary,
        }
        self._set_comparison_text(self.primary_compare, primary, side="primary")
        self._set_comparison_text(self.secondary_compare, secondary, side="secondary")
        count = len(detail.conflicts)
        self.compare_summary.configure(
            text=(
                f"{count} character-level conflict(s) from persisted alignment — "
                "click a highlighted span to focus its source line"
            )
        )

    def _set_comparison_text(
        self, widget: tk.Text, presentation: DiffPresentation, *, side: str | None = None
    ) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", presentation.text)
        for span in presentation.spans:
            start = f"1.0+{span.start}c"
            end = f"1.0+{span.end}c"
            widget.tag_add(span.kind.value, start, end)
            if side is not None:
                index_tag = f"conflict-index-{span.conflict_index}"
                widget.tag_add(index_tag, start, end)
                def on_conflict_click(
                    event: object,
                    selected_side: str = side,
                    index: int = span.conflict_index,
                ) -> str:
                    return self._on_conflict_click(selected_side, index)

                widget.tag_bind(index_tag, "<Button-1>", on_conflict_click)
        widget.configure(state="disabled")

    def _on_conflict_click(self, side: str, conflict_index: int) -> str:
        """Select one persisted diff and focus that engine's source line."""
        detail = self._current_detail
        if detail is None or not 0 <= conflict_index < len(detail.conflicts):
            return "break"
        self._selected_conflict_index = conflict_index
        for compare_side, widget in (
            ("primary", self.primary_compare),
            ("secondary", self.secondary_compare),
        ):
            widget.tag_remove("selected_conflict", "1.0", "end")
            presentation = self._comparison_presentations.get(compare_side)
            if presentation is None:
                continue
            for span in presentation.spans:
                if span.conflict_index == conflict_index:
                    widget.tag_add(
                        "selected_conflict",
                        f"1.0+{span.start}c",
                        f"1.0+{span.end}c",
                    )
                    widget.tag_raise("selected_conflict")
        conflict = detail.conflicts[conflict_index]
        summary = (
            f"conflict {conflict_index + 1}/{len(detail.conflicts)} "
            f"({conflict.kind.value}); {side} OCR line focused"
        )
        self._focus_source(side, status_text=f"{summary} — source geometry is line-level")
        return "break"

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
