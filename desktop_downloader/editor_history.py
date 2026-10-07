from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EditorSnapshot:
    start: str
    end: str
    crop: str
    rotate: str
    speed: str
    custom_x: str
    custom_y: str
    custom_w: str
    custom_h: str
    mute: bool
    volume: float
    fade_in: str
    fade_out: str


class EditorHistory:
    def __init__(self, limit: int = 60) -> None:
        self.limit = max(2, int(limit))
        self._items: list[EditorSnapshot] = []
        self._index = -1

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return 0 <= self._index < len(self._items) - 1

    @property
    def current(self) -> EditorSnapshot | None:
        if 0 <= self._index < len(self._items):
            return self._items[self._index]
        return None

    def clear(self) -> None:
        self._items.clear()
        self._index = -1

    def push(self, snapshot: EditorSnapshot) -> bool:
        current = self.current
        if current == snapshot:
            return False

        if self.can_redo:
            del self._items[self._index + 1 :]

        self._items.append(snapshot)
        if len(self._items) > self.limit:
            overflow = len(self._items) - self.limit
            del self._items[:overflow]
        self._index = len(self._items) - 1
        return True

    def undo(self) -> EditorSnapshot | None:
        if not self.can_undo:
            return None
        self._index -= 1
        return self._items[self._index]

    def redo(self) -> EditorSnapshot | None:
        if not self.can_redo:
            return None
        self._index += 1
        return self._items[self._index]
