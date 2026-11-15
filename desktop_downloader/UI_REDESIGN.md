# Desktop UI redesign: audit and changes (v3.16 → download-manager layout)

The goal is a main window that people who already use a Windows download manager (IDM, NDM and similar)
understand at once, without copying any product's name, branding or artwork. Only the interaction model
and information layout were followed. All icons are drawn in code (`icons.py`).

## 1. Audit of the v3.16 UI (A–Z)

| Area | v3.16 | Gap against a typical download manager |
|---|---|---|
| Layout | Sidebar with 4 pages (Download, Batch, Queue, Updates) and 3 tools (Editor, History, Settings). Each page is one scrolling canvas. | Downloads were split across 3 places (Download page "recent", Queue page, History page). A download manager has **one list** of all downloads, with the actions around it. |
| Information density | Downloads drawn as 78 px cards with progress bar and buttons. About 6–7 downloads visible at 1180×780. | IDM/NDM rows are about 21–25 px, so 20–30 downloads are visible. There were no columns to compare size, speed or date. |
| List columns | Title, chip, bar and one detail line (speed/size/ETA as free text). History page had a separate 6-column table. | No Size, Status %, Transfer rate, Time left, Last try / Date added, or Save To columns. No sorting or resizing in the live list. No column chooser. |
| Categories | None. History had filter chips (All/Completed/Failed/Video/Audio/Missing). | No tree for file type (Video, Audio, Compressed, Documents, Programs, Misc), Unfinished/Finished, or Queues. |
| Toolbar | None. Actions lived on each card (Pause/Cancel/Show in folder/Details) and on page headers (Pause all…). | No always-visible Add URL / Resume / Stop / Stop All / Delete / Delete Completed / Options toolbar that acts on the selection. |
| Menu bar | None. Ctrl+K command palette only. | No Tasks / File / Downloads / View / Help menus, which is where users look for less common actions. |
| Selection | Not possible. Every action was per card. | No row selection or multi-select, so bulk stop/resume/delete was impossible outside "all". |
| Context menu | Only in the History table. | Live downloads had no right-click menu. |
| Double-click / keyboard | History only (double-click, Del, Enter, Alt+Enter). | Live list was mouse-only. No Ctrl+N, Ctrl+B, F5 or Ctrl+F. |
| Status bar | Sidebar text "Local engine ready" and a status chip on the Download page. | No status bar with totals (downloading, queued, stopped, failed), total speed or extension state. |
| Dialogs | Add-link form was a full page. Settings and History replaced the main view. | Add URL, batch, options and properties are dialogs in download managers, so the list stays visible. |
| Empty states | Simple one-line text. | Each category needs its own empty message, and search needs "no matches". |
| Consistency | Four separate dark palettes (`ui_kit`, `settings_window`, `history_window`, `failure_dialog`/`command_palette`) with different hex values. Mixed Segoe UI sizes. | One palette and one type scale are needed for a product that looks finished. |
| Theme | Dark only. | Download managers default to a light, native-looking theme. Dark should be an option. |
| DPI / scaling | Canvas pages scale with `px()`. History/Settings used fixed point sizes. Icons came from the Segoe MDL2 font. | Icons have to be rendered at the real DPI. Table row heights and fonts have to follow the scale. |
| Accessibility | Canvas-drawn buttons cannot take keyboard focus. Low-contrast muted text on dark. | The main list must be a real focusable widget (arrow keys, Ctrl+A, Del, Enter, menu key). Contrast should be good in the light theme. |
| Tray | Works (show, pause all, resume all, quit). | No gap. Kept as is. |

## 2. What changed

**Main window** (`app_ui.py`, widgets in `main_window.py`)
- Menu bar: Tasks, File, Downloads, View (categories, details pane, columns, theme, refresh), Help.
- Toolbar with large icon + label buttons: Add URL, Batch, Resume, Stop, Start All, Stop All, Delete,
  Delete Completed, Options, Browsers (extension menu), Editor. Buttons enable and disable with the
  selection, and each has a tooltip with its shortcut. An "Update available" pill appears on the right.
- Category tree: All Downloads › Video, Audio, Compressed, Documents, Programs, Misc · Unfinished · Finished ·
  Queues › Main queue, plus one entry per batch, each with counts.
- One download table (ttk.Treeview) that merges live queue items and the history.
  - Columns: File Name (type icon with a status badge), Size, Status (percent while downloading), Transfer
    rate, Time left, Last Try Date, Date Added, Website, Quality, Save To.
  - Sort by clicking a column header, resize by dragging, and show or hide columns under View › Columns.
    Sort order, widths and visible columns are saved.
  - Multi-select, Ctrl+A, Del, Enter or double-click (opens a finished file, shows the error of a failed one,
    shows properties otherwise), Alt+Enter, Ctrl+C (copy link), and the right-click menu: Open, Open folder,
    Open in media editor, Resume, Stop, Cancel, Download again, Copy links, Open web page, Delete, Error
    details, Properties.
  - Delete asks for confirmation and offers "also delete the files from disk". Deleting a running download
    stops it and keeps it out of the history.
- Details pane for the selected download: progress bar, size, downloaded, rate, time left, website,
  quality, save path or error, and Pause/Resume, Cancel, Open, Open folder, Details buttons.
- Status bar: last message, totals, total speed, browser-extension state, version.
- Search box filters the current category.

**Dialogs**: Add URL (the former Download page, which pre-fills a link from the clipboard), Batch download (the
former Batch page; progress now appears under Queues), Updates & about, Options (the existing settings panel,
now in its own window), Properties and Error details (re-themed).

**Theme** (`theme.py`): one palette for everything, Light (default) and Dark, chosen in View › Theme or
Settings › General › Appearance. It applies on the next start. CustomTkinter dialogs use the same palette.

**Data** (`download_list.py`, unit tested in `tests/test_download_list.py`): row building, merging queue and
history, file-type categories, filters, search, sorting, enabled actions and totals. The engine's existing
`on_metrics` callback now feeds Size / Transfer rate / Time left. The queue snapshot exposes mode, folder,
page URL and quality. History entries store the `request_id` so a finished download replaces its live row.

Removed: `activity_view.py` (card list) and the in-window History page (`HistoryPage`). Its features are now
in the main list.

## 3. Not done yet (candidates for later)
- **Scheduler** (start/stop the queue at a set time). The toolbar has Start All / Stop All instead.
- Clipboard monitoring that offers to download copied links (IDM-style), drag-and-drop of links onto the
  window, and a per-download floating progress window.
- Live theme switching without restart. Canvas items copy colours when they are created.
- Bengali (and other) UI translations. All strings are still inline English.
- The native Windows menu bar stays light in the dark theme (a Windows limitation for Tk menus).
