"""Cut list for the media editor: blade cuts, segment states and output mapping.

The timeline is kept in *source* time. Cuts split the source into segments, and each
segment is in one of three states:

* ``keep``    - exported as is
* ``removed`` - ripple delete: left out, the following parts move up to close the gap
* ``gap``     - delete and leave the gap: black video and silence for the same length

Nothing here depends on Tk, so the logic is unit tested on any platform.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

KEEP = "keep"
REMOVED = "removed"
GAP = "gap"
STATES = (KEEP, REMOVED, GAP)

MIN_SEGMENT = 0.04  # seconds; no cut closer than this to another cut or to an edge


@dataclass(frozen=True)
class Segment:
    index: int
    start: float
    end: float
    state: str = KEEP

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


Piece = tuple[float, float, str]  # source start, source end, KEEP or GAP


class EditTimeline:
    def __init__(self, duration: float, cuts: Iterable[float] = (), states: Iterable[str] | None = None) -> None:
        self.duration = max(0.001, float(duration))
        self.cuts: list[float] = []
        self.states: list[str] = [KEEP]
        self.load(tuple(cuts), tuple(states) if states is not None else None)

    # ------------------------------------------------------------------ state in and out
    def snapshot(self) -> tuple[tuple[float, ...], tuple[str, ...]]:
        return tuple(round(cut, 6) for cut in self.cuts), tuple(self.states)

    def load(self, cuts: tuple[float, ...], states: tuple[str, ...] | None = None) -> None:
        clean: list[float] = []
        for cut in sorted(float(value) for value in cuts):
            if MIN_SEGMENT <= cut <= self.duration - MIN_SEGMENT and (not clean or cut - clean[-1] >= MIN_SEGMENT):
                clean.append(cut)
        self.cuts = clean
        wanted = list(states or ())
        if len(wanted) != len(clean) + 1 or any(state not in STATES for state in wanted):
            wanted = [KEEP] * (len(clean) + 1)
        self.states = wanted

    def reset(self) -> None:
        self.cuts = []
        self.states = [KEEP]

    @property
    def is_trivial(self) -> bool:
        """True when there are no cuts and nothing is deleted."""
        return not self.cuts and self.states == [KEEP]

    # ------------------------------------------------------------------ segments
    def segments(self) -> list[Segment]:
        bounds = [0.0, *self.cuts, self.duration]
        return [
            Segment(index, bounds[index], bounds[index + 1], self.states[index])
            for index in range(len(self.states))
        ]

    def segment(self, index: int) -> Segment:
        return self.segments()[index]

    def index_at(self, t: float) -> int:
        t = float(t)
        for index, cut in enumerate(self.cuts):
            if t < cut:
                return index
        return len(self.cuts)

    def can_split(self, t: float) -> bool:
        t = float(t)
        if t < MIN_SEGMENT or t > self.duration - MIN_SEGMENT:
            return False
        return all(abs(t - cut) >= MIN_SEGMENT for cut in self.cuts)

    def split_at(self, t: float) -> bool:
        """Blade cut at ``t``. Both halves keep the state of the segment that was cut."""
        if not self.can_split(t):
            return False
        index = self.index_at(t)
        self.cuts.insert(index, float(t))
        self.states.insert(index, self.states[index])
        return True

    def _valid(self, indices: Iterable[int]) -> list[int]:
        return sorted({index for index in indices if 0 <= index < len(self.states)})

    def set_state(self, indices: Iterable[int], state: str) -> bool:
        if state not in STATES:
            raise ValueError(f"Unknown segment state: {state}")
        changed = False
        for index in self._valid(indices):
            if self.states[index] != state:
                self.states[index] = state
                changed = True
        return changed

    def delete(self, indices: Iterable[int], *, ripple: bool) -> bool:
        return self.set_state(indices, REMOVED if ripple else GAP)

    def restore(self, indices: Iterable[int]) -> bool:
        return self.set_state(indices, KEEP)

    def close_gaps(self) -> bool:
        """Turn every gap into a ripple delete."""
        return self.set_state([i for i, state in enumerate(self.states) if state == GAP], REMOVED)

    def join(self, cut_index: int) -> bool:
        """Remove the cut between segment ``cut_index`` and the next one.

        The joined segment keeps the left segment's state.
        """
        if not 0 <= cut_index < len(self.cuts):
            return False
        del self.cuts[cut_index]
        del self.states[cut_index + 1]
        return True

    def move_cut(self, cut_index: int, t: float) -> float:
        """Roll edit: move one cut between its neighbours. Returns where it ended up."""
        if not 0 <= cut_index < len(self.cuts):
            raise IndexError(cut_index)
        low = (self.cuts[cut_index - 1] if cut_index > 0 else 0.0) + MIN_SEGMENT
        high = (self.cuts[cut_index + 1] if cut_index + 1 < len(self.cuts) else self.duration) - MIN_SEGMENT
        value = max(low, min(float(t), high))
        self.cuts[cut_index] = value
        return value

    def nearest_cut(self, t: float) -> int | None:
        if not self.cuts:
            return None
        return min(range(len(self.cuts)), key=lambda i: abs(self.cuts[i] - float(t)))

    # ------------------------------------------------------------------ output
    def pieces(self, start: float = 0.0, end: float | None = None) -> list[Piece]:
        """Parts of ``start``..``end`` that end up in the output, in order.

        Removed segments are left out, gaps are kept (marked GAP), and touching parts in the
        same state are merged so the export uses as few pieces as possible.
        """
        end = self.duration if end is None else float(end)
        start = max(0.0, float(start))
        end = min(self.duration, end)
        result: list[Piece] = []
        for segment in self.segments():
            if segment.state == REMOVED:
                continue
            a = max(start, segment.start)
            b = min(end, segment.end)
            if b - a <= 1e-6:
                continue
            if result and result[-1][2] == segment.state and abs(result[-1][1] - a) <= 1e-6:
                result[-1] = (result[-1][0], b, segment.state)
            else:
                result.append((a, b, segment.state))
        # A run of only gaps has nothing to show; neither does a trailing/leading gap alone.
        if all(piece[2] == GAP for piece in result):
            return []
        return result

    def output_duration(self, start: float = 0.0, end: float | None = None) -> float:
        return sum(b - a for a, b, _state in self.pieces(start, end))

    def removed_duration(self, start: float = 0.0, end: float | None = None) -> float:
        end = self.duration if end is None else float(end)
        return max(0.0, (min(end, self.duration) - max(0.0, start)) - self.output_duration(start, end))

    def state_at(self, t: float) -> str:
        return self.states[self.index_at(t)]

    def next_playable(self, t: float, end: float | None = None) -> float | None:
        """Where playback should continue from ``t``: skips ripple-deleted segments.

        Returns ``t`` itself when it is playable, or None when nothing playable is left
        before ``end``.
        """
        end = self.duration if end is None else min(self.duration, float(end))
        t = float(t)
        if t >= end:
            return None
        index = self.index_at(t)
        segments = self.segments()
        while index < len(segments) and segments[index].state == REMOVED:
            t = segments[index].end
            index += 1
        if index >= len(segments) or t >= end - 1e-6:
            return None
        return t

    def source_to_output(self, t: float, start: float = 0.0, end: float | None = None) -> float:
        """Position in the exported file for source time ``t`` (removed parts count as zero)."""
        total = 0.0
        for a, b, _state in self.pieces(start, end):
            if t <= a:
                break
            total += min(t, b) - a
            if t <= b:
                break
        return total

    def output_to_source(self, value: float, start: float = 0.0, end: float | None = None) -> float:
        remaining = max(0.0, float(value))
        pieces = self.pieces(start, end)
        for a, b, _state in pieces:
            if remaining <= b - a:
                return a + remaining
            remaining -= b - a
        return pieces[-1][1] if pieces else max(0.0, float(start))

    def pieces_from(self, position: float, max_output: float, start: float = 0.0,
                    end: float | None = None) -> list[Piece]:
        """Up to ``max_output`` seconds of output, starting at source ``position``."""
        result: list[Piece] = []
        left = max(0.0, float(max_output))
        for a, b, state in self.pieces(start, end):
            if b <= position:
                continue
            a = max(a, float(position))
            if left <= 1e-6:
                break
            b = min(b, a + left)
            result.append((a, b, state))
            left -= b - a
        if all(piece[2] == GAP for piece in result):
            return []
        return result


def snap(value: float, targets: Iterable[float], tolerance: float) -> float:
    """The closest target within ``tolerance`` of ``value``, else ``value``."""
    best = value
    best_distance = tolerance
    for target in targets:
        distance = abs(float(target) - value)
        if distance <= best_distance:
            best = float(target)
            best_distance = distance
    return best
