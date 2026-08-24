from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from loopmetry.io import load_jsonl
from loopmetry.schema import Event
from loopmetry.storage import EventStore, StorageError


ROOT = Path(__file__).resolve().parents[1]


def _event(
    event_id: str,
    *,
    provenance: list[dict],
    data: dict | None = None,
    schema_version: str = "0.2",
) -> Event:
    return Event.from_mapping(
        {
            "schema_version": schema_version,
            "event_id": event_id,
            "project_id": "proj",
            "session_id": "sess",
            "timestamp": "2026-08-23T10:00:00Z",
            "type": "note",
            "actor": "system",
            "source": "claude-code",
            "data": data if data is not None else {"summary": "x"},
            "provenance": provenance,
        }
    )


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
            by_id = {event.event_id: event for event in loaded}
            for event in events:
                self.assertEqual(by_id[event.event_id].provenance, event.provenance)

    def test_add_events_merges_provenance_on_duplicate_event_id(self) -> None:
        hook_provenance = [
            {"source": "claude-code", "capture_mode": "hook", "adapter_version": "1.0.0"}
        ]
        history_provenance = [
            {
                "source": "claude-code",
                "capture_mode": "history-backfill",
                "adapter_version": "1.0.0",
            }
        ]
        first = _event("evt-1", provenance=hook_provenance)
        second = _event("evt-1", provenance=history_provenance)
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "loopmetry.db"
            with EventStore(db_path) as store:
                store.add_events([first])
                second_result = store.add_events([second])
                loaded = store.list_events("proj")

            self.assertEqual(second_result.inserted, 0)
            self.assertEqual(second_result.merged, 1)
            self.assertEqual(second_result.skipped, 0)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(len(loaded[0].provenance), 2)
            sources = sorted(record.capture_mode.value for record in loaded[0].provenance)
            self.assertEqual(sources, ["history-backfill", "hook"])

    def test_legacy_row_is_upgraded_when_current_event_is_reingested(self) -> None:
        legacy = _event("evt-1", provenance=[], schema_version="0.1")
        current = _event(
            "evt-1",
            provenance=[
                {"source": "claude-code", "capture_mode": "hook", "adapter_version": "1.0.0"}
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "loopmetry.db"
            with EventStore(db_path) as store:
                store.add_events([legacy])
                result = store.add_events([current])
                loaded = store.list_events("proj")

            self.assertEqual(result.merged, 1)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].schema_version, "0.2")
            self.assertEqual(len(loaded[0].provenance), 1)

    def test_add_events_raises_on_genuine_content_conflict(self) -> None:
        first = _event("evt-1", provenance=[], data={"summary": "x"})
        second = _event("evt-1", provenance=[], data={"summary": "different"})
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "loopmetry.db"
            with EventStore(db_path) as store:
                store.add_events([first])
                with self.assertRaises(StorageError):
                    store.add_events([second])

    def test_list_events_round_trips_provenance(self) -> None:
        provenance = [
            {"source": "claude-code", "capture_mode": "hook", "adapter_version": "1.0.0"}
        ]
        event = _event("evt-1", provenance=provenance)
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "loopmetry.db"
            with EventStore(db_path) as store:
                store.add_events([event])
                loaded = store.list_events("proj")

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].provenance, event.provenance)

    def test_migration_adds_provenance_column_to_pre_fix_db(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "loopmetry.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE events (
                    event_id TEXT PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    source TEXT NOT NULL,
                    data_json TEXT NOT NULL
                )
                """
            )
            connection.commit()
            connection.close()

            with EventStore(db_path) as store:
                columns = {
                    row[1] for row in store._connection.execute("PRAGMA table_info(events)")
                }
                self.assertIn("provenance_json", columns)
                store.add_events([_event("evt-1", provenance=[])])
                self.assertEqual(len(store.list_events("proj")), 1)

    def test_concurrent_writers_do_not_crash_on_same_new_event_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "loopmetry.db"
            event = _event(
                "evt-1",
                provenance=[
                    {"source": "claude-code", "capture_mode": "hook", "adapter_version": "1.0.0"}
                ],
            )
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def write() -> None:
                try:
                    barrier.wait()
                    with EventStore(db_path) as store:
                        store.add_events([event])
                except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
                    errors.append(exc)

            threads = [threading.Thread(target=write) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            with EventStore(db_path) as store:
                loaded = store.list_events("proj")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].provenance, event.provenance)


if __name__ == "__main__":
    unittest.main()
