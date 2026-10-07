from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parents[1]
if str(DESKTOP_DIR) not in sys.path:
    sys.path.insert(0, str(DESKTOP_DIR))

from history_store import HistoryStore, make_history_entry


class HistoryStoreTests(unittest.TestCase):
    def test_add_and_list_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.mp4"
            second = root / "second.mp4"
            first.write_bytes(b"a")
            second.write_bytes(b"bb")
            store = HistoryStore(root / "history.json")

            one = make_history_entry(
                first,
                title="First",
                source_url="https://example.com/1",
                platform="youtube",
                mode="Video",
            )
            two = make_history_entry(
                second,
                title="Second",
                source_url="https://example.com/2",
                platform="facebook",
                mode="Video",
            )
            one["created_at"] = "2026-10-07T10:00:00+00:00"
            two["created_at"] = "2026-10-07T11:00:00+00:00"
            store.add(one)
            store.add(two)

            items = store.list()
            self.assertEqual([item["title"] for item in items], ["Second", "First"])
            self.assertEqual(items[0]["file_size"], 2)

    def test_duplicate_path_replaces_older_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "same.mp4"
            path.write_bytes(b"data")
            store = HistoryStore(root / "history.json")

            store.add(
                make_history_entry(
                    path,
                    title="Old title",
                    source_url="https://example.com/old",
                    platform="youtube",
                )
            )
            store.add(
                make_history_entry(
                    path,
                    title="New title",
                    source_url="https://example.com/new",
                    platform="youtube",
                )
            )

            items = store.list()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["title"], "New title")

    def test_prune_missing_keeps_existing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "exists.mp3"
            missing = root / "missing.mp3"
            existing.write_bytes(b"audio")
            store = HistoryStore(root / "history.json")

            store.add(
                make_history_entry(
                    existing,
                    title="Existing",
                    source_url="",
                    platform="instagram",
                    mode="Audio",
                )
            )
            store.add(
                make_history_entry(
                    missing,
                    title="Missing",
                    source_url="",
                    platform="instagram",
                    mode="Audio",
                )
            )

            self.assertEqual(store.prune_missing(), 1)
            self.assertEqual([item["title"] for item in store.list()], ["Existing"])

    def test_malformed_history_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.json"
            history.write_text("{not json", encoding="utf-8")
            store = HistoryStore(history)
            self.assertEqual(store.list(), [])

    def test_most_recent_existing_skips_missing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "exists.mp4"
            existing.write_bytes(b"ok")
            store = HistoryStore(root / "history.json")

            old = make_history_entry(
                existing,
                title="Existing",
                source_url="",
                platform="youtube",
            )
            old["created_at"] = "2026-10-07T09:00:00+00:00"
            missing = make_history_entry(
                root / "gone.mp4",
                title="Gone",
                source_url="",
                platform="youtube",
            )
            missing["created_at"] = "2026-10-07T10:00:00+00:00"
            store.add(old)
            store.add(missing)

            recent = store.most_recent_existing()
            self.assertIsNotNone(recent)
            self.assertEqual(recent["title"], "Existing")

    def test_store_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = HistoryStore(root / "history.json", limit=10)
            for index in range(15):
                path = root / f"{index}.mp4"
                store.add(
                    {
                        "file_path": str(path),
                        "title": str(index),
                        "created_at": f"2026-10-07T10:{index:02d}:00+00:00",
                    }
                )
            self.assertEqual(len(store.list()), 10)


if __name__ == "__main__":
    unittest.main()
