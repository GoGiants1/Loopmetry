from __future__ import annotations

import unittest
from pathlib import Path

from loopmetry.evaluation import ProjectEvaluator
from loopmetry.io import load_jsonl
from loopmetry.schema import Event


ROOT = Path(__file__).resolve().parents[1]


class ProjectEvaluatorTests(unittest.TestCase):
    def test_demo_project_produces_evidence_backed_metrics(self) -> None:
        events = load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        report = ProjectEvaluator().evaluate(events)

        self.assertEqual(report.project_id, "demo-expense-cli")
        self.assertEqual(report.snapshot.session_count, 2)
        self.assertEqual(report.snapshot.requirement_count, 2)
        self.assertEqual(report.snapshot.changed_file_count, 3)
        self.assertGreaterEqual(report.metric("traceability").score, 90.0)
        self.assertGreaterEqual(report.metric("verification_rigor").score, 85.0)
        self.assertGreaterEqual(report.metric("recovery_efficiency").score, 80.0)
        self.assertGreaterEqual(report.metric("change_discipline").score, 85.0)
        self.assertIn(report.steering.label, {"checkpoint-driven", "interactive"})
        self.assertNotIn("overall_score", report.to_mapping())

    def test_no_errors_is_provisional_not_perfect_confidence(self) -> None:
        events = [
            Event.from_mapping(
                {
                    "event_id": "evt-1",
                    "project_id": "p",
                    "session_id": "s",
                    "timestamp": "2026-08-22T09:00:00Z",
                    "type": "project_start",
                    "actor": "human",
                    "source": "test",
                    "data": {},
                }
            )
        ]
        metric = ProjectEvaluator().evaluate(events).metric("recovery_efficiency")
        self.assertEqual(metric.score, 100.0)
        self.assertLess(metric.confidence, 0.5)
        self.assertTrue(metric.gaps)

    def test_missing_verification_is_reported_as_gap(self) -> None:
        events = [
            Event.from_mapping(
                {
                    "event_id": "evt-1",
                    "project_id": "p",
                    "session_id": "s",
                    "timestamp": "2026-08-22T09:00:00Z",
                    "type": "requirement",
                    "actor": "human",
                    "source": "test",
                    "data": {"requirement_id": "REQ-1", "summary": "Do a thing"},
                }
            ),
            Event.from_mapping(
                {
                    "event_id": "evt-2",
                    "project_id": "p",
                    "session_id": "s",
                    "timestamp": "2026-08-22T09:01:00Z",
                    "type": "file_change",
                    "actor": "agent",
                    "source": "test",
                    "data": {"path": "app.py", "requirement_ids": ["REQ-1"]},
                }
            ),
        ]
        report = ProjectEvaluator().evaluate(events)
        metric = report.metric("verification_rigor")
        self.assertLess(metric.score, 30.0)
        self.assertTrue(any("No test" in gap for gap in metric.gaps))


if __name__ == "__main__":
    unittest.main()
