from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loopmetry.io import InputError, load_jsonl


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _base_event(**overrides: object) -> dict:
    event = {
        "schema_version": "0.2",
        "event_id": "evt-1",
        "project_id": "proj",
        "session_id": "sess",
        "timestamp": "2026-08-23T10:00:00Z",
        "type": "note",
        "actor": "system",
        "source": "claude-code",
        "data": {"summary": "x"},
        "provenance": [],
    }
    event.update(overrides)
    return event


class LoadJsonlDuplicateTests(unittest.TestCase):
    def test_same_file_duplicate_event_id_merges_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            _write_jsonl(
                path,
                [
                    _base_event(
                        provenance=[
                            {
                                "source": "claude-code",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ]
                    ),
                    _base_event(provenance=[]),
                ],
            )
            events = load_jsonl(path)
            self.assertEqual(len(events), 1)
            self.assertEqual(len(events[0].provenance), 1)

    def test_same_file_genuine_conflict_still_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            _write_jsonl(
                path,
                [
                    _base_event(data={"summary": "x"}),
                    _base_event(data={"summary": "different"}),
                ],
            )
            with self.assertRaises(InputError):
                load_jsonl(path)


if __name__ == "__main__":
    unittest.main()
