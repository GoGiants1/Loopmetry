from __future__ import annotations

import unittest

from loopmetry.event_merge import merge_events_tolerant
from loopmetry.schema import Event, EventType, Actor


def _event(event_id: str = "evt-1", summary: str = "x", provenance: list | None = None) -> Event:
    return Event.from_mapping(
        {
            "schema_version": "0.2",
            "event_id": event_id,
            "project_id": "proj",
            "session_id": "sess",
            "timestamp": "2026-08-23T10:00:00Z",
            "type": EventType.NOTE.value,
            "actor": Actor.SYSTEM.value,
            "source": "claude-code",
            "data": {"summary": summary},
            "provenance": provenance or [],
        }
    )


class MergeEventsTolerantTests(unittest.TestCase):
    def test_no_conflict_merges_and_reports_false(self) -> None:
        existing = _event(
            provenance=[
                {"source": "claude-code", "capture_mode": "hook", "adapter_version": "1.0.0"}
            ]
        )
        incoming = _event(
            provenance=[
                {"source": "claude-code", "capture_mode": "history-backfill", "adapter_version": "1.0.0"}
            ]
        )
        merged, conflicted = merge_events_tolerant(existing, incoming)
        self.assertFalse(conflicted)
        self.assertEqual(len(merged.provenance), 2)

    def test_true_no_op_reports_false_and_returns_existing(self) -> None:
        existing = _event()
        incoming = _event()
        merged, conflicted = merge_events_tolerant(existing, incoming)
        self.assertFalse(conflicted)
        self.assertIs(merged, existing)

    def test_genuine_conflict_reports_true_and_keeps_existing_unchanged(self) -> None:
        existing = _event(summary="x")
        incoming = _event(summary="different")
        merged, conflicted = merge_events_tolerant(existing, incoming)
        self.assertTrue(conflicted)
        self.assertIs(merged, existing)


if __name__ == "__main__":
    unittest.main()
