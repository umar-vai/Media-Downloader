from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hybrid_core.state_store import JsonStateStore


class StateStoreRecoveryTests(unittest.TestCase):
    def test_corrupt_primary_recovers_previous_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "jobs.json"
            store = JsonStateStore(path)

            first = [{"id": "one", "kind": "download", "status": "completed"}]
            second = [{"id": "two", "kind": "download", "status": "completed"}]
            store.save(first)
            store.save(second)

            path.write_text("{ definitely-not-json", encoding="utf-8")
            recovered = store.load()

            self.assertEqual(recovered, first)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["jobs"], first)
            quarantined = list(Path(folder).glob("jobs.corrupt-*.json"))
            self.assertTrue(quarantined)

    def test_atomic_save_keeps_valid_primary_when_no_backup_exists(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "jobs.json"
            store = JsonStateStore(path)
            jobs = [{"id": "abc", "kind": "download", "status": "queued"}]

            store.save(jobs)

            self.assertEqual(store.load(), jobs)
            self.assertFalse(path.with_suffix(".json.bak").exists())


if __name__ == "__main__":
    unittest.main()
