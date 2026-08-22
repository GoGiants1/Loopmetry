from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loopmetry.io import load_jsonl
from loopmetry.storage import EventStore


ROOT = Path(__file__).resolve().parents[1]


class EventStoreTests(unittest.TestCase):
    def test_ingest_is_idempotent(self) -> None:
        events = load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "loopmetry.db"
            with EventStore(db_path) as store:
                first = store.add_events(events)
                second = store.add_events(events)
                loaded = store.list_events("demo-expense-cli")
                projects = store.list_projects()

            self.assertEqual(first.inserted, len(events))
            self.assertEqual(first.skipped, 0)
            self.assertEqual(second.inserted, 0)
            self.assertEqual(second.skipped, len(events))
            self.assertEqual(len(loaded), len(events))
            self.assertEqual(projects, [("demo-expense-cli", len(events))])


if __name__ == "__main__":
    unittest.main()
