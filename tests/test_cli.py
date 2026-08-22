from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "loopmetry", *args],
            cwd=ROOT,
            text=True,
            input=stdin,
            capture_output=True,
            check=False,
            env=env,
        )

    def test_validate_analyze_and_bundle(self) -> None:
        source = str(ROOT / "examples" / "demo_project.jsonl")
        validated = self.run_cli("validate", source)
        self.assertEqual(validated.returncode, 0, validated.stderr)
        self.assertIn("20 event(s)", validated.stdout)

        analyzed = self.run_cli("analyze", source, "--format", "json")
        self.assertEqual(analyzed.returncode, 0, analyzed.stderr)
        self.assertIn('"project_id": "demo-expense-cli"', analyzed.stdout)
        self.assertNotIn("overall_score", analyzed.stdout)

        html = self.run_cli("analyze", source, "--format", "html")
        self.assertEqual(html.returncode, 0, html.stderr)
        self.assertIn("<!doctype html>", html.stdout)
        self.assertIn("No overall rank is produced", html.stdout)

        bundle = self.run_cli("bundle", source)
        self.assertEqual(bundle.returncode, 0, bundle.stderr)
        self.assertIn('"bundle_id": "sha256:', bundle.stdout)
        self.assertIn('"raw_prompts_included": false', bundle.stdout)

    def test_one_command_run_and_capture_hook(self) -> None:
        source = str(ROOT / "examples" / "demo_project.jsonl")
        with tempfile.TemporaryDirectory() as directory:
            output_root = Path(directory) / "runs"
            result = self.run_cli(
                "run",
                "--input",
                source,
                "--assignment-id",
                "course-2026",
                "--submitter-id",
                "S001",
                "--output-root",
                str(output_root),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("analysis complete", result.stdout)
            run_directories = list(output_root.iterdir())
            self.assertEqual(len(run_directories), 1)
            self.assertTrue((run_directories[0] / "report.html").is_file())
            self.assertTrue((run_directories[0] / "submission.json").is_file())

            captured = Path(directory) / "captured.jsonl"
            payload = {
                "session_id": "session-hook",
                "cwd": directory,
                "hook_event_name": "SessionEnd",
                "reason": "other",
            }
            hook = self.run_cli(
                "capture-hook",
                "--source",
                "codex",
                "--project-id",
                "demo",
                "--output",
                str(captured),
                stdin=json.dumps(payload),
            )
            self.assertEqual(hook.returncode, 0, hook.stderr)
            self.assertEqual(hook.stdout, "")
            self.assertIn('"project_id":"demo"', captured.read_text(encoding="utf-8"))

    def test_admin_roster_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            roster = root / "roster.csv"
            roster.write_text(
                "submitter_id,display_name\nS001,Alice\nS002,Bob\n",
                encoding="utf-8",
            )
            db_path = root / "admin.db"
            credentials = root / "credentials.csv"
            imported = self.run_cli(
                "admin",
                "import-roster",
                str(roster),
                "--db",
                str(db_path),
                "--assignment-id",
                "course-2026",
                "--output",
                str(credentials),
                "--server",
                "https://loopmetry.example.test",
            )
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertTrue(credentials.is_file())
            with credentials.open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertTrue(rows[0]["submission_token"].startswith("lm_"))
            self.assertIn("uvx --from", rows[0]["run_command"])
            self.assertIn("loopmetry run", rows[0]["run_command"])
            self.assertIn("$env:LOOPMETRY_SUBMISSION_TOKEN", rows[0]["run_command_powershell"])

            listed = self.run_cli(
                "admin",
                "list",
                "--db",
                str(db_path),
                "--assignment-id",
                "course-2026",
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertIn("not_submitted", listed.stdout)
            self.assertIn("S001", listed.stdout)

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
