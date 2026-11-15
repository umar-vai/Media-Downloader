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


def setup_style(root: tk.Misc) -> ttk.Style:
    import native

    style = native.setup(root)  # native Windows controls in the light theme
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
        tk.Label(tip, text=self.text, bg=C["SURFACE"], fg=C["TEXT"], font=ui.font(12), padx=px(8), pady=px(4),
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
        size = px(28)
        self._on = icons.tool(icon, size)
        self._off = icons.tool(icon, size, disabled=True)
        super().__init__(master, text=text, image=self._on, compound="top", bg=C["SURFACE"], fg=C["TEXT"],
                         font=ui.font(12), padx=px(9), pady=px(5), bd=0, highlightthickness=0)
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
            self.configure(bg=C["TOOL_HOVER"], cursor="hand2")

    def _leave(self, _e: Any = None) -> None:
        self._inside = False
        self.configure(bg=C["SURFACE"], cursor="")

    def _press(self, _e: Any = None) -> None:
        if self.enabled:
            self.configure(bg=C["TOOL_PRESS"])

    def _release(self, _e: Any = None) -> None:
        if not self.enabled:
            return
        self.configure(bg=C["TOOL_HOVER"] if self._inside else C["SURFACE"])
        if self._inside:
            self.command()

    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self.enabled:
            return
        self.enabled = on
        self.configure(image=self._on if on else self._off, fg=C["TEXT"] if on else C["FAINT"],
                       bg=(C["TOOL_HOVER"] if on and self._inside else C["SURFACE"]),
                       cursor="hand2" if on and self._inside else "")


class Toolbar(tk.Frame):
    def __init__(self, master: tk.Misc, icons: IconSet) -> None:
        super().__init__(master, bg=C["SURFACE"], highlightthickness=0, bd=0)
        self.icons = icons
        self.buttons: dict[str, ToolButton] = {}
        self.inner = tk.Frame(self, bg=C["SURFACE"])
        self.inner.pack(side="left", padx=px(6), pady=px(4))
        self.right = tk.Frame(self, bg=C["SURFACE"])
        self.right.pack(side="right", padx=px(12))

    def add(self, key: str, text: str, icon: str, command: Callable[[], Any], tip: str = "") -> ToolButton:
        button = ToolButton(self.inner, self.icons, text=text, icon=icon, command=command, tip=tip)
        button.pack(side="left", padx=px(1))
        self.buttons[key] = button
        return button

    def separator(self) -> None:
        tk.Frame(self.inner, bg=C["BORDER_SOFT"], width=1).pack(side="left", fill="y", padx=px(6), pady=px(8))


class UpdateBadge:
    """Toolbar pill shown when an update is available (keeps the old ``top_update_button`` API)."""

    def __init__(self, master: tk.Misc, icons: IconSet, command: Callable[[], Any]) -> None:
        self.label = tk.Label(master, text="Update available", image=icons.tool("update", px(18)), compound="left",
                              bg=C["SUCCESS_SOFT"], fg=C["SUCCESS"], font=ui.font(12, True), padx=px(10),
                              pady=px(5), cursor="hand2")
        self.label.bind("<ButtonRelease-1>", lambda _e: command())
        self.visible = False

    def configure(self, **kw: Any) -> None:
        if "text" in kw:
            self.label.configure(text=f" {kw['text']}")

    config = configure

    def show(self, on: bool = True) -> None:
        if on and not self.visible:
            self.label.pack(side="right", pady=px(14))
        elif not on and self.visible:
            self.label.pack_forget()
        self.visible = on

    def grid(self, *_a: Any, **_k: Any) -> None:
        self.show(True)

    def grid_remove(self, *_a: Any, **_k: Any) -> None:
        self.show(False)


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
        super().__init__(master, bg=C["SIDEBAR"], highlightthickness=0, bd=0)
        self._on_select = on_select
        self.icons = icons
        head = tk.Label(self, text="CATEGORIES", bg=C["SIDEBAR"], fg=C["FAINT"], font=ui.font(11, True), anchor="w")
        head.pack(fill="x", padx=px(14), pady=(px(10), px(4)))
        self.tree = ttk.Treeview(self, show="tree", style="Side.Treeview", selectmode="browse")
        self.tree.pack(fill="both", expand=True, padx=(px(4), 0))
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
class SearchBox(tk.Frame):
    def __init__(self, master: tk.Misc, on_change: Callable[[str], None]) -> None:
        super().__init__(master, bg=C["BORDER"], highlightthickness=0, bd=0)
        inner = tk.Frame(self, bg=C["SURFACE"])
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(inner, text="⌕", bg=C["SURFACE"], fg=C["FAINT"], font=ui.font(15)).pack(side="left",
                                                                                          padx=(px(8), 0))
        self.var = tk.StringVar()
        self.entry = tk.Entry(inner, textvariable=self.var, bg=C["SURFACE"], fg=C["TEXT"], relief="flat", bd=0,
                              insertbackground=C["TEXT"], font=ui.font(13), highlightthickness=0,
                              selectbackground=C["ACCENT"], selectforeground=C["ON_ACCENT"], width=26)
        self.entry.pack(side="left", fill="x", expand=True, padx=px(6), pady=px(5))
        self.clear = tk.Label(inner, text="✕", bg=C["SURFACE"], fg=C["FAINT"], font=ui.font(11), cursor="hand2")
        self.clear.bind("<ButtonRelease-1>", lambda _e: self.var.set(""))
        self._placeholder = "Search downloads"
        self._ph = False
        self.var.trace_add("write", lambda *_a: self._changed(on_change))
        self.entry.bind("<FocusIn>", lambda _e: self._placeholder_off())
        self.entry.bind("<FocusOut>", lambda _e: self._placeholder_on())
        self.entry.bind("<Escape>", lambda _e: self.var.set(""))
        self._placeholder_on()

    def _placeholder_on(self) -> None:
        if not self.var.get():
            self._ph = True
            self.entry.configure(fg=C["FAINT"])
            self.var.set(self._placeholder)

    def _placeholder_off(self) -> None:
        if self._ph:
            self._ph = False
            self.var.set("")
            self.entry.configure(fg=C["TEXT"])

    def _changed(self, callback: Callable[[str], None]) -> None:
        value = "" if self._ph else self.var.get()
        if value:
            self.clear.pack(side="right", padx=(0, px(8)))
        else:
            self.clear.pack_forget()
        callback(value)

    def focus(self) -> None:
        self.entry.focus_set()
        self.entry.selection_range(0, "end")


# --------------------------------------------------------------------------- download table
class DownloadTable(tk.Frame):
    def __init__(self, master: tk.Misc, icons: IconSet, *, on_select: Callable[[], None],
                 on_open: Callable[[], None], on_context: Callable[[Any, bool], None],
                 on_sort: Callable[[str], None], on_delete: Callable[[], None]) -> None:
        super().__init__(master, bg=C["SURFACE"], highlightthickness=0, bd=0)
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
        self.empty_title = tk.Label(self.empty, text="", bg=C["SURFACE"], fg=C["TEXT"], font=ui.font(15, True))
        self.empty_title.pack()
        self.empty_text = tk.Label(self.empty, text="", bg=C["SURFACE"], fg=C["MUTED"], font=ui.font(13),
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
class ProgressBar(tk.Canvas):
    """Glides to each new value; ``busy`` shows a moving segment (connecting, merging)."""

    def __init__(self, master: tk.Misc, height: int = 8) -> None:
        super().__init__(master, height=px(height), bg=C["SURFACE"], highlightthickness=0, bd=0)
        self.h = px(height)
        self.value = 0.0
        self.shown = 0.0
        self.busy = False
        self.phase = 0.0
        self.color = C["ACCENT"]
        self._job: str | None = None
        self.track = self.create_line(0, 0, 0, 0, width=self.h, capstyle="round", fill=C["SURFACE_3"])
        self.fill = self.create_line(0, 0, 0, 0, width=self.h, capstyle="round", fill=self.color)
        self.glow = self.create_line(0, 0, 0, 0, width=self.h, capstyle="round", fill=C["ACCENT_SOFT"],
                                     state="hidden")
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, value: float, color: str | None = None, *, busy: bool = False, animate: bool = True) -> None:
        value = max(0.0, min(1.0, float(value)))
        if color and color != self.color:
            self.color = color
            self.itemconfigure(self.fill, fill=color)
        if not animate or value < self.shown - 0.05:
            self.shown = value
        self.value = value
        self.busy = busy
        self._draw()
        self._schedule()

    def _schedule(self) -> None:
        moving = self.busy or abs(self.value - self.shown) > 0.001
        if moving and self._job is None:
            self._job = self.after(30, self._tick)

    def _tick(self) -> None:
        self._job = None
        if not self.winfo_exists():
            return
        diff = self.value - self.shown
        self.shown = self.value if abs(diff) < 0.001 else self.shown + diff * 0.2
        if self.busy:
            self.phase = (self.phase + 0.012) % 1.0
        self._draw()
        self._schedule()

    def _draw(self) -> None:
        w = max(1, self.winfo_width())
        r = self.h / 2
        left, right = r, max(r, w - r)
        self.coords(self.track, left, r, right, r)
        if self.shown <= 0.002:
            self.itemconfigure(self.fill, state="hidden")
        else:
            self.itemconfigure(self.fill, state="normal")
            self.coords(self.fill, left, r, left + (right - left) * self.shown, r)
        if not self.busy:
            self.itemconfigure(self.glow, state="hidden")
            return
        span = right - left
        seg = span * 0.22
        start = left + (span + seg) * self.phase - seg
        a, b = max(left, start), min(right, start + seg)
        if b > a:
            self.coords(self.glow, a, r, b, r)
            self.itemconfigure(self.glow, state="normal",
                               fill=C["ACCENT_SOFT"] if self.shown > 0.002 else self.color)
            self.tag_raise(self.glow)
        else:
            self.itemconfigure(self.glow, state="hidden")


class DetailsPane(tk.Frame):
    FIELDS = (("size", "Size"), ("done", "Downloaded"), ("speed", "Transfer rate"), ("eta", "Time left"),
              ("website", "Website"), ("quality", "Quality"))

    def __init__(self, master: tk.Misc, icons: IconSet, actions: dict[str, Callable[[], Any]]) -> None:
        super().__init__(master, bg=C["SURFACE"], highlightthickness=0, bd=0)
        self.icons = icons
        tk.Frame(self, bg=C["BORDER_SOFT"], height=1).pack(fill="x", side="top")
        body = tk.Frame(self, bg=C["SURFACE"])
        body.pack(fill="both", expand=True, padx=px(16), pady=(px(10), px(10)))
        body.grid_columnconfigure(1, weight=1)

        self.icon = tk.Label(body, bg=C["SURFACE"])
        self.icon.grid(row=0, column=0, rowspan=2, sticky="nw", padx=(0, px(12)))
        top = tk.Frame(body, bg=C["SURFACE"])
        top.grid(row=0, column=1, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        self.title = tk.Label(top, text="", bg=C["SURFACE"], fg=C["TEXT"], font=ui.font(14, True), anchor="w")
        self.title.grid(row=0, column=0, sticky="ew")
        self.state = tk.Label(top, text="", bg=C["SURFACE"], fg=C["MUTED"], font=ui.font(12, True), anchor="e")
        self.state.grid(row=0, column=1, sticky="e", padx=(px(12), 0))
        self.bar = ProgressBar(body, height=7)
        self.bar.grid(row=1, column=1, sticky="ew", pady=(px(7), px(6)))

        grid = tk.Frame(body, bg=C["SURFACE"])
        grid.grid(row=2, column=1, sticky="ew")
        self.values: dict[str, tk.Label] = {}
        for index, (key, label) in enumerate(self.FIELDS):
            tk.Label(grid, text=label, bg=C["SURFACE"], fg=C["FAINT"], font=ui.font(11), anchor="w").grid(
                row=0, column=index, sticky="w", padx=(0, px(26)))
            value = tk.Label(grid, text="—", bg=C["SURFACE"], fg=C["TEXT"], font=ui.font(12), anchor="w")
            value.grid(row=1, column=index, sticky="w", padx=(0, px(26)))
            self.values[key] = value
        self.where = tk.Label(body, text="", bg=C["SURFACE"], fg=C["MUTED"], font=ui.font(12), anchor="w")
        self.where.grid(row=3, column=1, sticky="ew", pady=(px(6), 0))

        buttons = tk.Frame(body, bg=C["SURFACE"])
        buttons.grid(row=0, column=2, rowspan=4, sticky="ne", padx=(px(18), 0))
        self.buttons: dict[str, ttk.Button] = {}
        for key, text in (("toggle", "Pause"), ("cancel", "Cancel"), ("open", "Open"), ("open_folder", "Open folder"),
                          ("more", "Details…")):
            button = ttk.Button(buttons, text=text, command=actions[key], width=14)
            button.pack(fill="x", pady=(0, px(5)))
            self.buttons[key] = button
        self._sig: tuple | None = None
        self.show(None, 0, "")

    def show(self, row: dict[str, Any] | None, count: int, summary: str) -> None:
        if row is None:
            sig: tuple = ("none", count, summary)
            if sig == self._sig:
                return
            self._sig = sig
            self.icon.configure(image=self.icons.tree("all", px(28)))
            self.title.configure(text=summary or "No download selected")
            self.state.configure(text="")
            self.bar.set(0, animate=False)
            for value in self.values.values():
                value.configure(text="—")
            self.where.configure(text="Select a download to see its progress. Double-click to open a finished file."
                                 if not count else "Use the toolbar or right-click for actions on the selection.")
            for key, button in self.buttons.items():
                button.configure(state="normal" if count and key in {"toggle", "cancel"} else "disabled",
                                 text="Pause" if key == "toggle" else button.cget("text"))
            return
        status = row["status"]
        colors = {"complete": C["SUCCESS"], "failed": C["DANGER"], "missing": C["WARNING"], "paused": C["WARNING"],
                  "stopping": C["WARNING"], "cancelled": C["MUTED"]}
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
        switched = self._sig is None or self._sig[0] != row["key"]
        self._sig = sig
        self.icon.configure(image=self.icons.file(row["category"], status, px(28)))
        self.title.configure(text=row["name"])
        self.state.configure(text=row["status_text"].upper(), fg=colors.get(status, C["ACCENT"]))
        if switched:  # another download was selected: jump, do not glide
            self.bar.set(row["progress"], colors.get(status, C["ACCENT"]), animate=False)
        self.bar.set(row["progress"], colors.get(status, C["ACCENT"]),
                     busy=status in {"connecting", "finishing", "retrying"})
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
    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, bg=C["HEADING"], highlightthickness=0, bd=0)
        tk.Frame(self, bg=C["BORDER_SOFT"], height=1).pack(fill="x", side="top")
        row = tk.Frame(self, bg=C["HEADING"])
        row.pack(fill="x")
        self.dot = tk.Label(row, text="●", bg=C["HEADING"], fg=C["SUCCESS"], font=ui.font(11))
        self.dot.pack(side="left", padx=(px(12), px(4)), pady=px(4))
        self.message = tk.Label(row, text="Ready", bg=C["HEADING"], fg=C["TEXT"], font=ui.font(12), anchor="w")
        self.message.pack(side="left", fill="x", expand=True)
        self.version = tk.Label(row, text="", bg=C["HEADING"], fg=C["FAINT"], font=ui.font(12))
        self.version.pack(side="right", padx=(px(10), px(12)))
        self.extension = tk.Label(row, text="", bg=C["HEADING"], fg=C["MUTED"], font=ui.font(12))
        self.extension.pack(side="right", padx=px(10))
        self.speed = tk.Label(row, text="", bg=C["HEADING"], fg=C["MUTED"], font=ui.font(12, True))
        self.speed.pack(side="right", padx=px(10))
        self.summary = tk.Label(row, text="", bg=C["HEADING"], fg=C["MUTED"], font=ui.font(12))
        self.summary.pack(side="right", padx=px(10))

    def set_message(self, text: str, color: str) -> None:
        self.message.configure(text=text or "Ready")
        self.dot.configure(fg=color)

    def set_totals(self, summary: str, speed: str) -> None:
        if self.summary.cget("text") != summary:
            self.summary.configure(text=summary)
        speed = f"↓ {speed}" if speed else ""
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
