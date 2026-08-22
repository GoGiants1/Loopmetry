from __future__ import annotations

import unittest

from loopmetry.schema import Event, EventType, SchemaError


class EventSchemaTests(unittest.TestCase):
    def base_event(self) -> dict[str, object]:
        return {
            "schema_version": "0.1",
            "event_id": "evt-1",
            "project_id": "project-a",
            "session_id": "session-a",
            "timestamp": "2026-08-22T09:00:00Z",
            "type": "file_change",
            "actor": "agent",
            "source": "test",
            "data": {
                "path": "src/app.py",
                "requirement_ids": ["REQ-1"],
            },
        }

    def test_parses_valid_event(self) -> None:
        event = Event.from_mapping(self.base_event())
        self.assertEqual(event.type, EventType.FILE_CHANGE)
        self.assertEqual(event.requirement_ids, ("REQ-1",))
        self.assertEqual(event.paths, ("src/app.py",))
        self.assertEqual(event.to_mapping()["timestamp"], "2026-08-22T09:00:00Z")

    def test_rejects_naive_timestamp(self) -> None:
        raw = self.base_event()
        raw["timestamp"] = "2026-08-22T09:00:00"
        with self.assertRaisesRegex(SchemaError, "timezone"):
            Event.from_mapping(raw)

    def test_verification_requires_valid_status(self) -> None:
        raw = self.base_event()
        raw["type"] = "verification"
        raw["actor"] = "tool"
        raw["data"] = {"kind": "test", "status": "green"}
        with self.assertRaisesRegex(SchemaError, "data.status"):
            Event.from_mapping(raw)


if __name__ == "__main__":
    unittest.main()
