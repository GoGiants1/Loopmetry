from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "loopmetry", *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_validate_and_analyze(self) -> None:
        source = str(ROOT / "examples" / "demo_project.jsonl")
        validated = self.run_cli("validate", source)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertIn("20 event(s)", validated.stdout)

        analyzed = self.run_cli("analyze", source, "--format", "json")
        self.assertEqual(analyzed.returncode, 0, analyzed.stderr)
        self.assertIn('"project_id": "demo-expense-cli"', analyzed.stdout)
        self.assertNotIn("overall_score", analyzed.stdout)

        visualized = self.run_cli("analyze", source, "--format", "html")
        self.assertEqual(visualized.returncode, 0, visualized.stderr)
        self.assertTrue(visualized.stdout.startswith("<!doctype html>"))
        self.assertIn('id="loopmetry-report"', visualized.stdout)

    def test_ingest_and_report(self) -> None:
        source = str(ROOT / "examples" / "demo_project.jsonl")
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "events.db")
            ingested = self.run_cli("ingest", source, "--db", db_path)
            self.assertEqual(ingested.returncode, 0, ingested.stderr)
            reported = self.run_cli(
                "report",
                "demo-expense-cli",
                "--db",
                db_path,
                "--format",
                "markdown",
            )
            self.assertEqual(reported.returncode, 0, reported.stderr)
            self.assertIn("Loopmetry project report", reported.stdout)


if __name__ == "__main__":
    unittest.main()
