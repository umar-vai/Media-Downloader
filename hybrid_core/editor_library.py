from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


PRESET_KEYS = {
    "crop_preset",
    "custom_x",
    "custom_y",
    "custom_width",
    "custom_height",
    "rotate",
    "speed",
    "mute",
    "volume_percent",
    "fade_in",
    "fade_out",
    "audio_preset",
    "noise_reduction",
    "quality",
}

PROJECT_KEYS = PRESET_KEYS | {
    "source_path",
    "output_dir",
    "output_name",
    "start",
    "end",
    "preview_position",
}


class EditorLibrary:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            raw = {}
        presets = raw.get("presets") if isinstance(raw, dict) else []
        projects = raw.get("projects") if isinstance(raw, dict) else []
        return {
            "presets": [item for item in (presets or []) if isinstance(item, dict)],
            "projects": [item for item in (projects or []) if isinstance(item, dict)],
        }

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps({"version": 1, **self._data}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.path)

    @staticmethod
    def _name(value: str) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            raise ValueError("A name is required.")
        return text[:80]

    @staticmethod
    def _filtered(payload: dict[str, Any], keys: set[str]) -> dict[str, Any]:
        return {key: payload[key] for key in keys if key in payload}

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            return {
                "presets": sorted(
                    (dict(item) for item in self._data["presets"]),
                    key=lambda item: float(item.get("updated_at") or 0),
                    reverse=True,
                ),
                "projects": sorted(
                    (dict(item) for item in self._data["projects"]),
                    key=lambda item: float(item.get("updated_at") or 0),
                    reverse=True,
                ),
            }

    def save_preset(self, name: str, settings: dict[str, Any], item_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            item = {
                "id": item_id or uuid.uuid4().hex,
                "name": self._name(name),
                "settings": self._filtered(dict(settings or {}), PRESET_KEYS),
                "updated_at": now,
            }
            existing = next((x for x in self._data["presets"] if x.get("id") == item["id"]), None)
            if existing:
                existing.update(item)
                item = dict(existing)
            else:
                self._data["presets"].append(item)
            self._data["presets"] = self._data["presets"][-50:]
            self._write()
            return dict(item)

    def delete_preset(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._data["presets"])
            self._data["presets"] = [item for item in self._data["presets"] if item.get("id") != item_id]
            changed = len(self._data["presets"]) != before
            if changed:
                self._write()
            return changed

    def save_project(self, name: str, data: dict[str, Any], item_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            item = {
                "id": item_id or uuid.uuid4().hex,
                "name": self._name(name),
                "data": self._filtered(dict(data or {}), PROJECT_KEYS),
                "updated_at": now,
            }
            existing = next((x for x in self._data["projects"] if x.get("id") == item["id"]), None)
            if existing:
                existing.update(item)
                item = dict(existing)
            else:
                self._data["projects"].append(item)
            self._data["projects"] = self._data["projects"][-50:]
            self._write()
            return dict(item)

    def delete_project(self, item_id: str) -> bool:
        with self._lock:
            before = len(self._data["projects"])
            self._data["projects"] = [item for item in self._data["projects"] if item.get("id") != item_id]
            changed = len(self._data["projects"]) != before
            if changed:
                self._write()
            return changed
