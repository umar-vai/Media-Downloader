from __future__ import annotations

import threading
import unittest

from pathlib import Path
import sys

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from media_player import EmbeddedMediaPlayer


class _AliveProcess:
    def poll(self):
        return None


class MediaPlayerReadinessTests(unittest.TestCase):
    def make_player(self) -> EmbeddedMediaPlayer:
        player = EmbeddedMediaPlayer.__new__(EmbeddedMediaPlayer)
        player._lock = threading.RLock()
        player._closed = False
        player._ready = threading.Event()
        player._ready.set()
        player._fatal_error = ""
        player._last_command_error = ""
        player._last_status = "paused"
        player._latest_position = 1.0
        player._process = _AliveProcess()
        return player

    def test_nonfatal_ipc_error_does_not_clear_ready_state(self) -> None:
        player = self.make_player()
        player._handle_message({"error": "property unavailable"})
        self.assertTrue(player.ready)
        self.assertEqual(player.fatal_error, "")
        self.assertEqual(player.last_error, "property unavailable")

    def test_decode_error_is_fatal(self) -> None:
        player = self.make_player()
        player._handle_message({"event": "end-file", "reason": "error"})
        self.assertFalse(player.ready)
        self.assertIn("decode", player.fatal_error.lower())

    def test_property_updates_do_not_clear_ready_state(self) -> None:
        player = self.make_player()
        player._handle_message({"event": "property-change", "name": "pause", "data": False})
        player._handle_message({"event": "property-change", "name": "time-pos", "data": 12.5})
        self.assertTrue(player.ready)
        self.assertFalse(player.is_paused())
        self.assertAlmostEqual(player.position() or 0.0, 12.5)


if __name__ == "__main__":
    unittest.main()
