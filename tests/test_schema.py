from __future__ import annotations

import unittest

from loopmetry.schema import CaptureMode, Event, EventType, SchemaError


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


class ProvenanceTests(unittest.TestCase):
    def _base_raw(self) -> dict:
        return {
            "event_id": "evt-1",
            "project_id": "proj",
            "session_id": "sess",
            "timestamp": "2026-08-23T10:00:00Z",
            "type": "note",
            "actor": "system",
            "source": "claude-code",
            "data": {"summary": "x"},
        }

    def test_event_without_provenance_defaults_to_empty(self) -> None:
        event = Event.from_mapping(self._base_raw())
        self.assertEqual(event.provenance, ())
        self.assertNotIn("provenance", event.to_mapping())

    def test_schema_0_1_events_still_load(self) -> None:
        raw = self._base_raw()
        raw["schema_version"] = "0.1"
        event = Event.from_mapping(raw)
        self.assertEqual(event.schema_version, "0.1")

    def test_provenance_round_trip(self) -> None:
        raw = self._base_raw()
        raw["schema_version"] = "0.2"
        raw["provenance"] = [
            {
                "source": "claude-code",
                "capture_mode": "hook",
                "adapter_version": "1.0.0",
            },
            {
                "source": "claude-code",
                "capture_mode": "history-backfill",
                "adapter_version": "1.0.0",
                "source_ref": {"session_file_sha256": "abc", "record_index": 7},
            },
        ]
        event = Event.from_mapping(raw)
        self.assertEqual(len(event.provenance), 2)
        self.assertIs(event.provenance[0].capture_mode, CaptureMode.HOOK)
        self.assertEqual(
            event.provenance[1].source_ref, {"session_file_sha256": "abc", "record_index": 7}
        )
        self.assertEqual(event.to_mapping()["provenance"], raw["provenance"])

    def test_invalid_capture_mode_is_rejected(self) -> None:
        raw = self._base_raw()
        raw["provenance"] = [
            {"source": "x", "capture_mode": "guessed", "adapter_version": "1"}
        ]
        with self.assertRaises(SchemaError):
            Event.from_mapping(raw)

    def test_provenance_must_be_a_list_of_objects(self) -> None:
        raw = self._base_raw()
        raw["provenance"] = {"capture_mode": "hook"}
        with self.assertRaises(SchemaError):
            Event.from_mapping(raw)

    def test_unsupported_schema_version_is_rejected(self) -> None:
        raw = self._base_raw()
        raw["schema_version"] = "0.3"
        with self.assertRaises(SchemaError):
            Event.from_mapping(raw)


if __name__ == "__main__":
    unittest.main()
