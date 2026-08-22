from __future__ import annotations

import unittest
from pathlib import Path

from loopmetry.evaluation import ProjectEvaluator
from loopmetry.io import load_jsonl
from loopmetry.report import render_html
from loopmetry.schema import Event


ROOT = Path(__file__).resolve().parents[1]


class HtmlReportTests(unittest.TestCase):
    def test_demo_report_is_standalone(self) -> None:
        report = ProjectEvaluator().evaluate(
            load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        )
        output = render_html(report)

        self.assertTrue(output.startswith("<!doctype html>"))
        self.assertIn("demo-expense-cli", output)
        self.assertNotIn("<script src=", output)
        self.assertNotIn("https://", output)

    def test_user_controlled_text_is_escaped(self) -> None:
        event = Event.from_mapping(
            {
                "event_id": "evt-<script>",
                "project_id": "project-<img src=x onerror=alert(1)>",
                "session_id": "s",
                "timestamp": "2026-08-22T09:00:00Z",
                "type": "requirement",
                "actor": "human",
                "source": "test",
                "data": {
                    "requirement_id": "REQ-1",
                    "summary": "<script>alert('x')</script>",
                },
            }
        )
        output = render_html(ProjectEvaluator().evaluate([event]))

        self.assertNotIn("<script>alert", output)
        self.assertNotIn("<img src=x", output)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", output)


if __name__ == "__main__":
    unittest.main()
