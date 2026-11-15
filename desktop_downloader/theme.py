"""One colour palette for the whole app, light (default) or dark.

The palette is chosen once at start-up from ``settings.json`` (key ``theme``), before any
window is built, because canvas items and widgets copy their colours when created.
Changing the theme in Settings therefore applies the next time the app starts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

THEMES = ("Light", "Dark")

# Light = the Windows system colours (ButtonFace, Window, Highlight, GrayText...), so the main
# window looks like any classic Windows program (IDM / NDM style), with no custom polish.
LIGHT: dict[str, str] = {
    "BG": "#F0F0F0",            # COLOR_BTNFACE: window / dialog / toolbar background
    "SIDEBAR": "#FFFFFF",       # COLOR_WINDOW: category tree
    "SURFACE": "#FFFFFF",       # COLOR_WINDOW: lists, entries
    "SURFACE_2": "#FFFFFF",
    "SURFACE_3": "#E5E5E5",     # progress track, placeholders
    "BORDER": "#ABADB3",        # entry / list border
    "BORDER_SOFT": "#D5D5D5",   # etched separators
    "TEXT": "#000000",          # COLOR_WINDOWTEXT
    "MUTED": "#444444",
    "FAINT": "#6D6D6D",         # COLOR_GRAYTEXT
    "ACCENT": "#0066CC",        # COLOR_HOTLIGHT (links)
    "ACCENT_HOVER": "#004C99",
    "ACCENT_SOFT": "#CCE8FF",
    "ON_ACCENT": "#FFFFFF",
    "SELECT": "#0078D7",        # COLOR_HIGHLIGHT
    "SELECT_TEXT": "#FFFFFF",   # COLOR_HIGHLIGHTTEXT
    "CYAN": "#0066CC",
    "ACTION": "#0078D7",
    "ACTION_HOVER": "#005A9E",
    "ON_ACTION": "#FFFFFF",
    "SUCCESS": "#008000",
    "SUCCESS_SOFT": "#DFF6DD",
    "WARNING": "#9D5D00",
    "WARNING_SOFT": "#FFF4CE",
    "DANGER": "#C00000",
    "DANGER_SOFT": "#FDE7E9",
    "DANGER_HOVER": "#F9D3D7",
    "DANGER_LINE": "#E8A0A8",
    "INFO_SOFT": "#E5F3FF",
    "STRIPE": "#FFFFFF",
    "HEADING": "#F0F0F0",
    "TOOL_HOVER": "#E5F3FF",    # Windows toolbar hot-tracking
    "TOOL_PRESS": "#CCE8FF",
    "TOOL_EDGE": "#99D1FF",
}

DARK: dict[str, str] = {
    "BG": "#0B0F17",
    "SIDEBAR": "#0E131D",
    "SURFACE": "#121826",
    "SURFACE_2": "#171E2E",
    "SURFACE_3": "#1F2840",
    "BORDER": "#29344D",
    "BORDER_SOFT": "#1C2538",
    "TEXT": "#EEF2FF",
    "MUTED": "#8F9AB4",
    "FAINT": "#5D6782",
    "ACCENT": "#7C6CFF",
    "ACCENT_HOVER": "#6A59F0",
    "ACCENT_SOFT": "#28224F",
    "ON_ACCENT": "#FFFFFF",
    "SELECT": "#2E2766",
    "SELECT_TEXT": "#FFFFFF",
    "CYAN": "#2DD4FF",
    "ACTION": "#2DD4FF",
    "ACTION_HOVER": "#1BB8E0",
    "ON_ACTION": "#031018",
    "SUCCESS": "#34D399",
    "SUCCESS_SOFT": "#0F2E26",
    "WARNING": "#FBBF24",
    "WARNING_SOFT": "#33290F",
    "DANGER": "#FB7185",
    "DANGER_SOFT": "#361721",
    "DANGER_HOVER": "#4A1D2B",
    "DANGER_LINE": "#5B2433",
    "INFO_SOFT": "#13233F",
    "STRIPE": "#141B2A",
    "HEADING": "#171E2E",
    "TOOL_HOVER": "#1D2538",
    "TOOL_PRESS": "#252F48",
    "TOOL_EDGE": "#29344D",
}


def _settings_file() -> Path:
    return Path(os.getenv("APPDATA") or Path.home()) / "MediaDownloader" / "settings.json"


def normalize_theme(value: Any) -> str:
    text = str(value or "").strip().title()
    return text if text in THEMES else "Light"


def saved_theme(path: Path | None = None) -> str:
    """The theme stored in settings.json, or Light when there is none."""
    try:
        raw = json.loads((path or _settings_file()).read_text(encoding="utf-8"))
    except Exception:
        return "Light"
    return normalize_theme(raw.get("theme") if isinstance(raw, dict) else None)


def palette(name: str) -> dict[str, str]:
    return dict(DARK if normalize_theme(name) == "Dark" else LIGHT)


NAME = saved_theme()
C: dict[str, str] = palette(NAME)
IS_DARK = NAME == "Dark"


def rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


# Ready-made keyword sets for CustomTkinter widgets, so dialogs follow the palette.
def ctk_button(kind: str = "secondary") -> dict[str, Any]:
    if kind == "primary":
        return {"fg_color": C["ACCENT"], "hover_color": C["ACCENT_HOVER"], "text_color": C["ON_ACCENT"]}
    if kind == "danger":
        return {"fg_color": "transparent", "hover_color": C["DANGER_SOFT"], "text_color": C["DANGER"],
                "border_width": 1, "border_color": C["DANGER_LINE"]}
    if kind == "ghost":
        return {"fg_color": "transparent", "hover_color": C["SURFACE_3"], "text_color": C["MUTED"],
                "border_width": 1, "border_color": C["BORDER"]}
    return {"fg_color": C["SURFACE_3"], "hover_color": C["BORDER"], "text_color": C["TEXT"]}


def ctk_option() -> dict[str, Any]:
    return {
        "fg_color": C["SURFACE"], "button_color": C["SURFACE_3"], "button_hover_color": C["BORDER"],
        "text_color": C["TEXT"], "dropdown_fg_color": C["SURFACE"], "dropdown_text_color": C["TEXT"],
        "dropdown_hover_color": C["SURFACE_3"],
    }


def ctk_entry() -> dict[str, Any]:
    return {"fg_color": C["SURFACE"], "border_color": C["BORDER"], "text_color": C["TEXT"]}
