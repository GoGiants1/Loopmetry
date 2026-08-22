from __future__ import annotations

import json
import unittest
from pathlib import Path

from loopmetry.evaluation import ProjectEvaluator
from loopmetry.io import load_jsonl
from loopmetry.report import _safe_embedded_json, render_html


ROOT = Path(__file__).resolve().parents[1]


class HtmlReportTests(unittest.TestCase):
    def test_html_report_is_self_contained_and_embeds_report_json(self) -> None:
        events = load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        report = ProjectEvaluator().evaluate(events)

        output = render_html(report)

        self.assertTrue(output.startswith("<!doctype html>"))
        self.assertIn("demo-expense-cli", output)
        self.assertIn('id="loopmetry-report"', output)
        self.assertNotIn("https://", output)

        embedded = output.split(
            '<script id="loopmetry-report" type="application/json">', 1
        )[1].split("</script>", 1)[0]
        payload = json.loads(embedded)
        self.assertEqual(payload["project_id"], "demo-expense-cli")
        self.assertNotIn("overall_score", payload)

    def test_embedded_json_escapes_script_terminators(self) -> None:
        malicious = "</script><script>alert(1)</script>"
        encoded = _safe_embedded_json({"value": malicious})

        self.assertNotIn("</script>", encoded.lower())
        self.assertEqual(json.loads(encoded)["value"], malicious)


if __name__ == "__main__":
    unittest.main()
