"""Widgets of the main window: toolbar, category tree, download table, details pane and
status bar. They only draw and report clicks; ``app_ui.UIMixin`` connects them to the
download engine.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

import download_list as dl
import ui_kit as ui
from icons import IconSet
from theme import C
from ui_kit import px


_root: tk.Misc | None = None


def sysfont(bold: bool = False) -> Any:
    """The Windows message font (Segoe UI 9 pt), the one every native control uses."""
    import native

    if _root is None:
        return ("Segoe UI", 9, "bold" if bold else "normal")
    return native.font(_root, 0, bold)


def setup_style(root: tk.Misc) -> ttk.Style:
    import native

    global _root
    _root = root
    style = native.setup(root)  # native Windows controls in the light theme
    if native.NATIVE:
        # Leave the list views to the "vista" theme: real Windows column headers, scroll bars,
        # sunken borders and the system highlight colour, like Explorer / IDM / NDM.
        # Compact rows, like a Windows list view in Details mode (IDM / NDM / Explorer).
        row = px(18)
        style.configure("MD.Treeview", rowheight=row, font=sysfont())
        style.configure("Side.Treeview", rowheight=row, font=sysfont(), indent=px(19))
        style.configure("MD.Horizontal.TProgressbar", thickness=px(15))
        return style
    body = ui.font(13)
    heading = ui.font(12, True)
    style.configure("MD.Treeview", background=C["SURFACE"], fieldbackground=C["SURFACE"], foreground=C["TEXT"],
                    rowheight=px(25), borderwidth=0, relief="flat", font=body)
    style.map("MD.Treeview", background=[("selected", C["SELECT"])], foreground=[("selected", C["SELECT_TEXT"])])
    style.layout("MD.Treeview", [("MD.Treeview.treearea", {"sticky": "nswe"})])
    style.configure("MD.Treeview.Heading", background=C["HEADING"], foreground=C["MUTED"], font=heading,
                    relief="flat", borderwidth=1, bordercolor=C["BORDER_SOFT"], lightcolor=C["HEADING"],
                    darkcolor=C["BORDER_SOFT"], padding=(px(6), px(5)))
    style.map("MD.Treeview.Heading", background=[("active", C["SURFACE_3"])],
              foreground=[("active", C["TEXT"])])

    style.configure("Side.Treeview", background=C["SIDEBAR"], fieldbackground=C["SIDEBAR"], foreground=C["TEXT"],
                    rowheight=px(27), borderwidth=0, relief="flat", font=body, indent=px(16))
    style.map("Side.Treeview", background=[("selected", C["ACCENT_SOFT"])],
              foreground=[("selected", C["TEXT"])])
    style.layout("Side.Treeview", [("Side.Treeview.treearea", {"sticky": "nswe"})])

    style.configure("MD.Horizontal.TProgressbar", troughcolor=C["SURFACE_3"], background=C["SUCCESS"],
                    bordercolor=C["BORDER"], lightcolor=C["SUCCESS"], darkcolor=C["SUCCESS"], thickness=px(15))

    for orient in ("Vertical", "Horizontal"):
        name = f"MD.{orient}.TScrollbar"
        style.configure(name, troughcolor=C["SURFACE"], background=C["SURFACE_3"], bordercolor=C["SURFACE"],
                        lightcolor=C["SURFACE_3"], darkcolor=C["SURFACE_3"], arrowcolor=C["MUTED"],
                        gripcount=0, arrowsize=px(12), relief="flat")
        style.map(name, background=[("active", C["BORDER"]), ("pressed", C["BORDER"])])
    return style


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: Any = None) -> None:
        self._cancel()
        self._job = self.widget.after(650, self._show)

    def _cancel(self) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self) -> None:
        self._job = None
        if self._tip is not None or not self.text:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.configure(bg=C["BORDER"])
        tk.Label(tip, text=self.text, bg=C["SURFACE"], fg=C["TEXT"], font=sysfont(), padx=px(5), pady=px(2),
                 justify="left").pack(padx=1, pady=1)
        x = self.widget.winfo_rootx() + px(6)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(4)
        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self, _event: Any = None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# --------------------------------------------------------------------------- toolbar
class ToolButton(tk.Label):
    def __init__(self, master: tk.Misc, icons: IconSet, *, text: str, icon: str, command: Callable[[], Any],
                 tip: str = "") -> None:
        # Small toolbar glyphs with the caption underneath: a compact classic Windows toolbar.
        size = px(20)
        self._on = icons.tool(icon, size)
        self._off = icons.tool(icon, size, disabled=True)
        super().__init__(master, text=text, image=self._on, compound="top", bg=C["BG"], fg=C["TEXT"],
                         font=sysfont(), padx=px(5), pady=px(1), bd=0, highlightthickness=1,
                         highlightbackground=C["BG"], highlightcolor=C["BG"])
        self.command = command
        self.enabled = True
        self._inside = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        if tip:
            Tooltip(self, tip)

    def _enter(self, _e: Any = None) -> None:
        self._inside = True
        if self.enabled:
            self._paint(C["TOOL_HOVER"], C["TOOL_EDGE"])

    def _paint(self, fill: str, edge: str) -> None:
        self.configure(bg=fill, highlightbackground=edge, highlightcolor=edge)

    def _leave(self, _e: Any = None) -> None:
        self._inside = False
        self._paint(C["BG"], C["BG"])

    def _press(self, _e: Any = None) -> None:
        if self.enabled:
            self._paint(C["TOOL_PRESS"], C["TOOL_EDGE"])

    def _release(self, _e: Any = None) -> None:
        if not self.enabled:
            return
        if self._inside:
            self._paint(C["TOOL_HOVER"], C["TOOL_EDGE"])
        else:
            self._paint(C["BG"], C["BG"])
        if self._inside:
            self.command()

    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        self.configure(image=self._on if on else self._off, fg=C["TEXT"] if on else C["FAINT"])
        if on and self._inside:
            self._paint(C["TOOL_HOVER"], C["TOOL_EDGE"])
        else:
            self._paint(C["BG"], C["BG"])


class Toolbar(tk.Frame):
    def __init__(self, master: tk.Misc, icons: IconSet) -> None:
        super().__init__(master, bg=C["BG"], highlightthickness=0, bd=0)
        self.icons = icons
        self.buttons: dict[str, ToolButton] = {}
        self.inner = tk.Frame(self, bg=C["BG"])
        self.inner.pack(side="left", padx=px(2), pady=px(2))
        self.right = tk.Frame(self, bg=C["BG"])
        self.right.pack(side="right", padx=px(4))
        self.bind("<Configure>", lambda _e: self._fit(), add="+")

    def _fit(self) -> None:
        # In a narrow window the buttons win: optional text on the right (the update note) is
        # hidden instead of being squeezed into a stray letter.
        # Measure with the note always counted, so showing/hiding it cannot flip the result.
        wanted = sum(getattr(child, "full_width", child.winfo_reqwidth)() for child in self.right.winfo_children())
        tight = self.inner.winfo_reqwidth() + wanted + px(16) > self.winfo_width()
        for child in self.right.winfo_children():
            compact = getattr(child, "set_compact", None)
            if compact is not None:
                compact(tight)

    def add(self, key: str, text: str, icon: str, command: Callable[[], Any], tip: str = "") -> ToolButton:
        button = ToolButton(self.inner, self.icons, text=text, icon=icon, command=command, tip=tip)
        button.pack(side="left", padx=px(1))
        self.buttons[key] = button
        return button

    def separator(self) -> None:
        ttk.Separator(self.inner, orient="vertical").pack(side="left", fill="y", padx=px(3), pady=px(3))


class UpdateBadge(tk.Frame):
    """Always-visible "Updates" button at the right end of the toolbar, with a one-line result
    under it: checking, up to date, a new version (green, click to install) or an error.

    Keeps the old ``top_update_button`` API: ``configure(text=...)`` + ``grid()`` announce an
    update, ``grid_remove()`` returns to the neutral state."""

    def __init__(self, master: tk.Misc, icons: IconSet, command: Callable[[], Any],
                 check: Callable[[], Any] | None = None, version: str = "") -> None:
        super().__init__(master, bg=C["BG"], highlightthickness=0, bd=0)
        self._open_updates = command
        self._check = check or command
        self.version = version
        self.available = False
        self.button = ToolButton(self, icons, text="Updates", icon="update", command=self._clicked,
                                 tip="Check for a new version (one click)")
        self.button.pack(side="right")
        self.note = tk.Label(self, text="", bg=C["BG"], fg=C["FAINT"], font=sysfont())
        self.note.pack(side="right", padx=(0, px(4)))
        self.note.bind("<ButtonRelease-1>", lambda _e: self._clicked())
        self.pack(side="right")
        self.set_state("idle")

    def full_width(self) -> int:
        return self.button.winfo_reqwidth() + self.note.winfo_reqwidth() + px(4)

    def set_compact(self, on: bool) -> None:
        shown = bool(self.note.winfo_manager())
        if on and shown and not self.available:
            self.note.pack_forget()
        elif (not on or self.available) and not shown:
            self.note.pack(side="right", padx=(0, px(4)))

    def _clicked(self) -> None:
        if self.available:
            self._open_updates()
        else:
            self._check()

    def set_state(self, state: str, text: str = "") -> None:
        """idle | checking | current | available | ready | error"""
        self.available = state in {"available", "ready"}
        version = f"v{self.version}" if self.version else ""
        texts = {
            "idle": "Click to check",
            "checking": "Checking…",
            "current": f"Up to date {version}".strip(),
            "available": text or "Update available",
            "ready": text or "Restart to update",
            "error": text or "Check failed — retry",
        }
        colors = {"available": C["SUCCESS"], "ready": C["SUCCESS"], "error": C["DANGER"], "checking": C["MUTED"]}
        self.note.configure(text=texts.get(state, text), fg=colors.get(state, C["FAINT"]),
                            font=sysfont(state in {"available", "ready"}))
        self.button.configure(text="Install update" if self.available else "Updates")
        self.button.set_enabled(state != "checking")

    def configure(self, cnf: Any = None, **kw: Any) -> Any:  # type: ignore[override]
        if "text" in kw and not cnf:
            text = str(kw.pop("text"))
            self.set_state("ready" if "restart" in text.lower() else "available", text)
            if not kw:
                return None
        return super().configure(cnf, **kw)

    config = configure  # type: ignore[assignment]

    def show(self, on: bool = True) -> None:
        if not on:
            self.set_state("idle")

    def grid(self, *_a: Any, **_k: Any) -> None:  # type: ignore[override]
        pass  # always visible; configure(text=...) already set the message

    def grid_remove(self, *_a: Any, **_k: Any) -> None:  # type: ignore[override]
        if self.available:
            self.set_state("idle")


# --------------------------------------------------------------------------- category tree
TREE_LAYOUT: list[tuple[str, str, str, str]] = [
    # iid, parent, label, icon
    ("all", "", "All Downloads", "all"),
    *[(key, "all", label, key) for key, label in dl.FILE_CATEGORIES],
    ("unfinished", "", "Unfinished", "unfinished"),
    ("finished", "", "Finished", "finished"),
    ("queues", "", "Queues", "queues"),
    ("queue:active", "queues", "Main queue", "queue"),
]


class CategoryTree(tk.Frame):
    def __init__(self, master: tk.Misc, icons: IconSet, on_select: Callable[[str], None]) -> None:
        super().__init__(master, bg=C["BG"], highlightthickness=0, bd=0)
        self._on_select = on_select
        self.icons = icons
        self.tree = ttk.Treeview(self, show="tree", style="Side.Treeview", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=(px(3), 0), pady=(0, px(3)))
        self.tree.column("#0", width=px(200))
        self._labels: dict[str, str] = {}
        size = px(16)
        for iid, parent, label, icon in TREE_LAYOUT:
            self.tree.insert(parent, "end", iid=iid, text=f" {label}", image=icons.tree(icon, size), open=True)
            self._labels[iid] = label
        self._batches: list[str] = []
        self._suppress = False
        self.tree.bind("<<TreeviewSelect>>", self._changed)

    def _changed(self, _e: Any = None) -> None:
        if self._suppress:
            return
        selection = self.tree.selection()
        if selection:
            key = selection[0]
            self._on_select("queue:active" if key == "queues" else key)

    def select(self, key: str) -> None:
        if not self.tree.exists(key):
            key = "all"
        self._suppress = True
        try:
            self.tree.selection_set(key)
            self.tree.see(key)
        finally:
            self._suppress = False

    def update_counts(self, counts: dict[str, int], groups: list[dict[str, Any]]) -> None:
        for iid, _parent, label, _icon in TREE_LAYOUT:
            if iid == "queues":
                continue
            value = counts.get(iid, 0)
            text = f" {label}" + (f"  ({value})" if value else "")
            if self._labels.get(iid) != text:
                self._labels[iid] = text
                self.tree.item(iid, text=text)
        wanted = [group["key"] for group in groups]
        for key in [k for k in self._batches if k not in wanted]:
            if self.tree.exists(key):
                if key in self.tree.selection():
                    self.select("queue:active")
                    self._on_select("queue:active")
                self.tree.delete(key)
        size = px(16)
        for index, group in enumerate(groups):
            text = f" {group['label']}  ({group['done']}/{group['total']})"
            if not self.tree.exists(group["key"]):
                self.tree.insert("queues", index + 1, iid=group["key"], text=text, image=self.icons.tree("queue", size))
            elif self._labels.get(group["key"]) != text:
                self.tree.item(group["key"], text=text)
            self._labels[group["key"]] = text
        self._batches = wanted

    def title_of(self, key: str) -> str:
        if key.startswith("queue:") and key != "queue:active":
            return self._labels.get(key, " Batch").strip().split("  (")[0]
        return {"queue:active": "Main queue"}.get(key) or dict(
            (iid, label) for iid, _p, label, _i in TREE_LAYOUT).get(key, "All Downloads")


# --------------------------------------------------------------------------- list header + search
class SearchBox(ttk.Frame):
    """"Search: [__________]" with a standard Windows edit box. Esc clears it."""

    def __init__(self, master: tk.Misc, on_change: Callable[[str], None]) -> None:
        super().__init__(master)
        ttk.Label(self, text="Search:").pack(side="left", padx=(0, px(5)))
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=28)
        self.entry.pack(side="left", fill="x", expand=True)
        self.var.trace_add("write", lambda *_a: on_change(self.var.get()))
        self.entry.bind("<Escape>", lambda _e: self.var.set(""))

    def focus(self) -> None:
        self.entry.focus_set()
        self.entry.selection_range(0, "end")


# --------------------------------------------------------------------------- download table
class DownloadTable(tk.Frame):
    def __init__(self, master: tk.Misc, icons: IconSet, *, on_select: Callable[[], None],
                 on_open: Callable[[], None], on_context: Callable[[Any, bool], None],
                 on_sort: Callable[[str], None], on_delete: Callable[[], None]) -> None:
        super().__init__(master, bg=C["BG"], highlightthickness=0, bd=0)
        self.icons = icons
        data_columns = [key for key in dl.COLUMN_KEYS if key != "name"]
        self.tree = ttk.Treeview(self, columns=data_columns, style="MD.Treeview", selectmode="extended")
        ysb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview, style="MD.Vertical.TScrollbar")
        xsb = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview, style="MD.Horizontal.TScrollbar")
        self.tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.headings = {key: title for key, title, _w, _a in dl.COLUMNS}
        for key, title, width, anchor in dl.COLUMNS:
            column = "#0" if key == "name" else key
            self.tree.heading(column, text=title, anchor="w" if anchor == "w" else "e",
                              command=lambda k=key: on_sort(k))
            self.tree.column(column, width=px(width), minwidth=px(48), anchor=anchor, stretch=key == "name")
        self.tree.tag_configure("failed", foreground=C["DANGER"])
        self.tree.tag_configure("missing", foreground=C["WARNING"])
        self.tree.tag_configure("dim", foreground=C["MUTED"])
        self._sig: dict[str, tuple] = {}
        self._order: list[str] = []

        self.empty = tk.Frame(self.tree, bg=C["SURFACE"])
        self.empty_title = tk.Label(self.empty, text="", bg=C["SURFACE"], fg=C["FAINT"], font=sysfont())
        self.empty_title.pack()
        self.empty_text = tk.Label(self.empty, text="", bg=C["SURFACE"], fg=C["FAINT"], font=sysfont(),
                                   justify="center")
        self.empty_text.pack(pady=(px(6), 0))

        self.tree.bind("<<TreeviewSelect>>", lambda _e: on_select())
        self.tree.bind("<Double-1>", lambda e: on_open() if self.tree.identify_region(e.x, e.y) in {"cell", "tree"}
                       else None)
        self.tree.bind("<Return>", lambda _e: on_open())
        self.tree.bind("<Delete>", lambda _e: on_delete())
        self.tree.bind("<Control-a>", lambda _e: (self.tree.selection_set(self.tree.get_children()), "break")[1])
        self.tree.bind("<Button-3>", lambda e: self._context(e, on_context))
        self.tree.bind("<App>", lambda e: on_context(e, True))

    def _context(self, event: Any, callback: Callable[[Any, bool], None]) -> None:
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
            self.tree.focus(row)
        elif not row:
            self.tree.selection_set(())
        callback(event, bool(row))

    # columns ---------------------------------------------------------------
    def set_visible(self, columns: list[str]) -> None:
        self.tree.configure(displaycolumns=columns)

    def set_widths(self, widths: dict[str, Any]) -> None:
        for key, value in (widths or {}).items():
            column = "#0" if key == "name" else key
            if key in dl.COLUMN_KEYS:
                try:
                    self.tree.column(column, width=max(px(48), px(int(value))))
                except (TypeError, ValueError, tk.TclError):
                    pass

    def widths(self) -> dict[str, int]:
        result = {}
        for key in dl.COLUMN_KEYS:
            column = "#0" if key == "name" else key
            result[key] = int(round(int(self.tree.column(column, "width")) / max(ui.SCALE, 0.01)))
        return result

    def mark_sort(self, column: str, descending: bool) -> None:
        for key, title in self.headings.items():
            text = title + ((" ▼" if descending else " ▲") if key == column else "")
            self.tree.heading("#0" if key == "name" else key, text=text)

    # rows --------------------------------------------------------------------
    @staticmethod
    def _tags(row: dict[str, Any]) -> tuple[str, ...]:
        status = row["status"]
        if status == "failed":
            return ("failed",)
        if status == "missing":
            return ("missing",)
        if status in {"paused", "cancelled", "queued"}:
            return ("dim",)
        return ()

    def sync(self, rows: list[dict[str, Any]]) -> None:
        tree = self.tree
        keys = [row["key"] for row in rows]
        wanted = set(keys)
        gone = [key for key in self._sig if key not in wanted]
        if gone:
            tree.delete(*gone)
            for key in gone:
                self._sig.pop(key, None)
        size = px(16)
        for index, row in enumerate(rows):
            key = row["key"]
            values = (row["size_text"], row["status_text"], row["speed_text"], row["eta_text"], row["last_try_text"],
                      row["added_text"], row["website"], row["quality"], row["save_to"])
            tags = self._tags(row)
            sig = (row["name"], values, row["category"], row["status"], tags)
            old = self._sig.get(key)
            if old is None:
                tree.insert("", index, iid=key, text=f" {row['name']}", values=values, tags=tags,
                            image=self.icons.file(row["category"], row["status"], size))
            elif old != sig:
                options: dict[str, Any] = {"values": values, "tags": tags}
                if old[0] != row["name"]:
                    options["text"] = f" {row['name']}"
                if old[2:4] != sig[2:4]:
                    options["image"] = self.icons.file(row["category"], row["status"], size)
                tree.item(key, **options)
            self._sig[key] = sig
        if list(tree.get_children()) != keys:
            for index, key in enumerate(keys):
                tree.move(key, "", index)
        self._order = keys

    def show_empty(self, title: str, text: str) -> None:
        if title:
            self.empty_title.configure(text=title)
            self.empty_text.configure(text=text)
            self.empty.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.empty.place_forget()

    def selected(self) -> list[str]:
        return list(self.tree.selection())

    def select(self, keys: list[str]) -> None:
        keys = [key for key in keys if self.tree.exists(key)]
        self.tree.selection_set(keys)
        if keys:
            self.tree.focus(keys[0])
            self.tree.see(keys[0])

    def focus_table(self) -> None:
        self.tree.focus_set()
        if not self.tree.focus() and self._order:
            self.tree.focus(self._order[0])


# --------------------------------------------------------------------------- details pane
class DetailsPane(tk.Frame):
    """A plain Windows details area under the list: name and status, the standard progress
    bar, one line of "Label: value" facts, and the save path with a row of push buttons."""

    FIELDS = (("size", "Size"), ("done", "Downloaded"), ("speed", "Transfer rate"), ("eta", "Time left"),
              ("website", "Website"), ("quality", "Quality"))

    def __init__(self, master: tk.Misc, icons: IconSet, actions: dict[str, Callable[[], Any]]) -> None:
        bg = C["BG"]
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self.icons = icons
        ttk.Separator(self, orient="horizontal").pack(side="top", fill="x")
        body = tk.Frame(self, bg=bg)
        body.pack(fill="both", expand=True, padx=px(6), pady=(px(4), px(5)))
        body.grid_columnconfigure(0, weight=1)

        top = tk.Frame(body, bg=bg)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        self.title = tk.Label(top, text="", bg=bg, fg=C["TEXT"], font=sysfont(True), anchor="w")
        self.title.grid(row=0, column=0, sticky="ew")
        self.state = tk.Label(top, text="", bg=bg, fg=C["TEXT"], font=sysfont(), anchor="e")
        self.state.grid(row=0, column=1, sticky="e", padx=(px(12), 0))

        # The standard Windows progress bar (vista theme): no custom colours or effects.
        self.bar = ttk.Progressbar(body, orient="horizontal", mode="determinate", maximum=1000,
                                   style="MD.Horizontal.TProgressbar")
        self.bar.grid(row=1, column=0, sticky="ew", pady=(px(3), px(3)))
        self._busy = False

        facts = tk.Frame(body, bg=bg)
        facts.grid(row=2, column=0, sticky="ew")
        self._facts = facts
        self.values: dict[str, tk.Label] = {}
        self._pairs: list[tuple[tk.Label, tk.Label]] = []
        for key, label in self.FIELDS:
            name = tk.Label(facts, text=label + ":", bg=bg, fg=C["MUTED"], font=sysfont(), anchor="w")
            value = tk.Label(facts, text="—", bg=bg, fg=C["TEXT"], font=sysfont(), anchor="w")
            self._pairs.append((name, value))
            self.values[key] = value
        self._per_row = 0

        bottom = tk.Frame(body, bg=bg)
        bottom.grid(row=3, column=0, sticky="ew", pady=(px(4), 0))
        bottom.grid_columnconfigure(0, weight=1)
        self.where = tk.Label(bottom, text="", bg=bg, fg=C["MUTED"], font=sysfont(), anchor="w")
        self.where.grid(row=0, column=0, sticky="ew", padx=(0, px(8)))
        buttons = tk.Frame(bottom, bg=bg)
        buttons.grid(row=0, column=1, sticky="e")
        self._buttons_row = buttons
        self._stacked = False
        body.bind("<Configure>", lambda e: self._reflow(e.width), add="+")
        self.buttons: dict[str, ttk.Button] = {}
        for key, text in (("toggle", "Pause"), ("cancel", "Cancel"), ("open", "Open"), ("open_folder", "Open folder"),
                          ("more", "Properties…")):
            button = ttk.Button(buttons, text=text, command=actions[key], width=13 if key == "more" else 11)
            button.pack(side="left", padx=(px(4), 0))
            self.buttons[key] = button
        self._sig: tuple | None = None
        self.show(None, 0, "")

    def _reflow(self, width: int) -> None:
        # Wide window: one line of facts and the buttons beside the save path. Narrow window
        # (or large display scaling): facts wrap to two lines and the buttons go under the path.
        widths = [name.winfo_reqwidth() + value.winfo_reqwidth() + px(17) for name, value in self._pairs]
        per_row = len(self._pairs) if sum(widths) <= width else (len(self._pairs) + 1) // 2
        if per_row != self._per_row:
            self._per_row = per_row
            for index, (name, value) in enumerate(self._pairs):
                row, col = divmod(index, per_row)
                name.grid(row=row, column=col * 2, sticky="w", padx=(0 if col == 0 else px(14), px(3)))
                value.grid(row=row, column=col * 2 + 1, sticky="w")
        stacked = self._buttons_row.winfo_reqwidth() + px(260) > width
        if stacked != self._stacked:
            self._stacked = stacked
            if stacked:
                self._buttons_row.grid(row=1, column=0, columnspan=2, sticky="e", pady=(px(4), 0))
            else:
                self._buttons_row.grid(row=0, column=1, columnspan=1, sticky="e", pady=0)

    def _set_bar(self, value: float, busy: bool) -> None:
        # Connecting / merging with no progress yet: the standard Windows "marquee" bar.
        if busy and value <= 0.002:
            if not self._busy:
                self.bar.configure(mode="indeterminate")
                self.bar.start(25)
                self._busy = True
            return
        if self._busy:
            self.bar.stop()
            self.bar.configure(mode="determinate")
            self._busy = False
        self.bar.configure(value=max(0.0, min(1.0, float(value))) * 1000)

    def show(self, row: dict[str, Any] | None, count: int, summary: str) -> None:
        if row is None:
            sig: tuple = ("none", count, summary)
            if sig == self._sig:
                return
            self._sig = sig
            self.title.configure(text=summary or "No download selected")
            self.state.configure(text="")
            self._set_bar(0, False)
            for value in self.values.values():
                value.configure(text="—")
            self.where.configure(text="Select a download to see its progress. Double-click to open a finished file."
                                 if not count else "Use the toolbar or right-click for actions on the selection.",
                                 fg=C["MUTED"])
            for key, button in self.buttons.items():
                button.configure(state="normal" if count and key in {"toggle", "cancel"} else "disabled",
                                 text="Pause" if key == "toggle" else button.cget("text"))
            return
        status = row["status"]
        # Only problems get a colour (like Windows' own red error text); everything else is plain.
        colors = {"failed": C["DANGER"], "missing": C["DANGER"]}
        done = row["done_bytes"] if row["source"] == "queue" else row["size"]
        if status == "complete":
            done = row["size"]
        values = {
            "size": row["size_text"] or "—",
            "done": dl.format_size(done) or ("—" if row["progress"] <= 0 else f"{row['progress'] * 100:.1f}%"),
            "speed": row["speed_text"] or "—",
            "eta": row["eta_text"] or "—",
            "website": row["website"] or "—",
            "quality": row["quality"] or "—",
        }
        detail = row["detail"] if status in {"failed", "retrying", "connecting", "stopping"} else ""
        where = f"Save to:  {row['save_to'] or '—'}"
        if detail:
            where = f"{detail[:180]}"
        actions = dl.available_actions([row])
        toggle = "Resume" if "resume" in actions else "Pause"
        sig = (row["key"], row["name"], row["status_text"], row["progress"], tuple(values.values()), where,
               tuple(sorted(actions)))
        if sig == self._sig:
            return
        self._sig = sig
        self.title.configure(text=row["name"])
        self.state.configure(text=row["status_text"], fg=colors.get(status, C["TEXT"]))
        self._set_bar(row["progress"], status in {"connecting", "finishing", "retrying"})
        for key, label in self.values.items():
            label.configure(text=values[key])
        self.where.configure(text=where, fg=C["DANGER"] if status == "failed" else C["MUTED"])
        enabled = {
            "toggle": bool({"resume", "stop"} & actions),
            "cancel": "cancel" in actions,
            "open": "open" in actions,
            "open_folder": "open_folder" in actions,
            "more": "details" in actions or "properties" in actions,
        }
        for key, button in self.buttons.items():
            button.configure(state="normal" if enabled[key] else "disabled")
        self.buttons["toggle"].configure(text=toggle)
        self.buttons["more"].configure(text="Error details…" if "details" in actions else "Properties…")


# --------------------------------------------------------------------------- status bar
class StatusBar(tk.Frame):
    """A Windows status bar: text panes separated by etched lines, on the window colour."""

    def __init__(self, master: tk.Misc) -> None:
        bg = C["BG"]
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        ttk.Separator(self, orient="horizontal").pack(fill="x", side="top")
        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")

        def pane() -> tk.Label:
            label = tk.Label(row, text="", bg=bg, fg=C["TEXT"], font=sysfont())
            label.pack(side="right", padx=px(8), pady=px(2))
            ttk.Separator(row, orient="vertical").pack(side="right", fill="y", pady=px(2))
            return label

        self.dot = tk.Label(row, text="", bg=bg)  # kept for set_message(); a status bar has no dot
        self.version = pane()
        self.extension = pane()
        self.speed = pane()
        self.summary = pane()
        self.message = tk.Label(row, text="Ready", bg=bg, fg=C["TEXT"], font=sysfont(), anchor="w")
        self.message.pack(side="left", fill="x", expand=True, padx=(px(6), px(4)))

    def set_message(self, text: str, color: str) -> None:
        self.message.configure(text=text or "Ready")
        self.dot.configure(fg=color)

    def set_totals(self, summary: str, speed: str) -> None:
        if self.summary.cget("text") != summary:
            self.summary.configure(text=summary)
        speed = f"Speed: {speed}" if speed else ""
        if self.speed.cget("text") != speed:
            self.speed.configure(text=speed)


# --------------------------------------------------------------------------- dialogs
class ConfirmDelete:
    """Delete confirmation with an "also delete the files" option, like a download manager."""

    @staticmethod
    def ask(master: tk.Misc, *, count: int, files: int, finished_only: bool = False) -> tuple[bool, bool]:
        import native

        dialog = native.Dialog(master, "Delete completed" if finished_only else "Delete downloads", modal=True)
        noun = f"{count} download{'s' if count != 1 else ''}"
        text = (f"Remove {noun} that finished from the list?" if finished_only
                else f"Remove {noun} from the list?")
        body = dialog.body
        ttk.Label(body, image="::tk::icons::warning").grid(row=0, column=0, rowspan=3, sticky="nw",
                                                           padx=(0, native.PAD))
        ttk.Label(body, text=text, style="Title.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(body, text="Unfinished downloads are stopped first. Files stay on the disk unless you tick the "
                             "box below.", wraplength=380, justify="left").grid(
            row=1, column=1, sticky="w", pady=(native.GAP, native.PAD))
        delete_files = tk.BooleanVar(dialog, value=False)
        box = ttk.Checkbutton(body, text=f"Also delete {files} downloaded file{'s' if files != 1 else ''} from "
                                         "the disk (cannot be undone)", variable=delete_files)
        box.grid(row=2, column=1, sticky="w")
        if not files:
            box.configure(state="disabled")
        result = {"ok": False}

        def confirm() -> None:
            result["ok"] = True
            dialog.close()

        dialog.add_button("Cancel", dialog.close)
        dialog.add_button("Delete", confirm, default=True)
        dialog.show()
        master.wait_window(dialog)
        return bool(result["ok"]), bool(result["ok"] and delete_files.get())
