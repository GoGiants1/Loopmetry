from __future__ import annotations

import unittest
from pathlib import Path

from loopmetry.io import load_jsonl
from loopmetry.llm_bundle import BundleError, build_evaluation_bundle
from loopmetry.schema import Event


ROOT = Path(__file__).resolve().parents[1]


class EvaluationBundleTests(unittest.TestCase):
    def test_bundle_is_stable_and_excludes_raw_commands(self) -> None:
        events = load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        first = build_evaluation_bundle(events)
        second = build_evaluation_bundle(reversed(events))

        self.assertEqual(first["bundle_id"], second["bundle_id"])
        self.assertEqual(first, second)
        self.assertFalse(first["policy"]["raw_command_text_included"])
        serialized = str(first)
        self.assertNotIn("python -m unittest", serialized)
        self.assertNotIn("ruff check src tests", serialized)
        self.assertNotIn("overall_score", serialized)

    def test_absolute_paths_are_redacted(self) -> None:
        event = Event.from_mapping(
            {
                "event_id": "evt-1",
                "project_id": "p",
                "session_id": "s",
                "timestamp": "2026-08-22T09:00:00Z",
                "type": "file_change",
                "actor": "agent",
                "source": "test",
                "data": {
                    "path": "/Users/alice/secret/customer/app.py",
                    "summary": "Update <script>alert('x')</script>",
                },
            }
        )
        bundle = build_evaluation_bundle([event])
        bundled_event = bundle["events"][0]

        self.assertEqual(
            bundled_event["paths"], ["<absolute-path-redacted>/app.py"]
        )
        self.assertNotIn("alice", str(bundle))

    def test_explicit_event_budget_fails_closed(self) -> None:
        events = load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        with self.assertRaises(BundleError):
            build_evaluation_bundle(events, max_events=2)


if __name__ == "__main__":
    unittest.main()
