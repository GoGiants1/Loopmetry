from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from loopmetry.adapters.claude_code_history import encode_claude_project_dir
from loopmetry.io import load_jsonl

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

    def _make_history_project(self, tmp: Path) -> tuple[Path, Path]:
        root = tmp / "work" / "project"
        root.mkdir(parents=True)
        claude_home = tmp / "claude-home"
        project_dir = claude_home / "projects" / encode_claude_project_dir(root)
        project_dir.mkdir(parents=True)
        record = {
            "type": "user",
            "sessionId": "sess-1",
            "timestamp": "2026-08-20T09:00:00Z",
            "cwd": str(root),
            "message": {"role": "user", "content": "hello"},
        }
        (project_dir / "sess.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        return root, claude_home

    def test_history_discover_lists_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            result = self.run_cli(
                "history",
                "discover",
                "--source",
                "claude-code",
                "--root",
                str(root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sess.jsonl", result.stdout)

    def test_history_import_requires_consent_when_not_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            result = self.run_cli(
                "history",
                "import",
                "--source",
                "claude-code",
                "--root",
                str(root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
        self.assertEqual(result.returncode, 2)
        self.assertFalse((root / ".loopmetry" / "events").exists())

    def test_history_import_fails_closed_on_corrupt_existing_output(self) -> None:
        # A pre-existing output file that fails to parse must never be treated
        # as "no prior evidence" and silently overwritten with only this run's
        # events -- that would delete everything previously imported.
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
            output.parent.mkdir(parents=True)
            output.write_text("{not valid json\n", encoding="utf-8")
            original = output.read_text(encoding="utf-8")

            result = self.run_cli(
                "history",
                "import",
                "--source",
                "claude-code",
                "--root",
                str(root),
                "--yes",
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(output.read_text(encoding="utf-8"), original)
            checkpoint = root / ".loopmetry" / "checkpoints" / "claude-code-history.json"
            self.assertFalse(checkpoint.exists())

    def test_history_import_with_yes_writes_events_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            result = self.run_cli(
                "history",
                "import",
                "--source",
                "claude-code",
                "--root",
                str(root),
                "--yes",
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
            events = load_jsonl(output)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].provenance[0].capture_mode.value, "history-backfill")
            checkpoint = root / ".loopmetry" / "checkpoints" / "claude-code-history.json"
            self.assertTrue(checkpoint.exists())

    def test_history_import_twice_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            args = (
                "history",
                "import",
                "--source",
                "claude-code",
                "--root",
                str(root),
                "--yes",
            )
            env = {**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)}
            first = self.run_cli(*args, env=env)
            second = self.run_cli(*args, env=env)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
            self.assertEqual(len(load_jsonl(output)), 1)


if __name__ == "__main__":
    unittest.main()
