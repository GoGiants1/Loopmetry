from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from loopmetry.adapters.claude_code_history import ClaudeCodeHistoryAdapter, encode_claude_project_dir
from loopmetry.cli import main
from loopmetry.io import load_jsonl
from loopmetry.minimize import derive_project_id

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

    def test_history_import_with_custom_output_does_not_chmod_its_parent(self) -> None:
        # atomic_write_bytes must only lock down a directory Loopmetry itself
        # just created (e.g. the default .loopmetry/events/), never a
        # pre-existing, possibly shared directory a custom --output happens to
        # live in -- even the project root itself, when --output is a bare
        # relative filename.
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            root.chmod(0o755)
            before = stat.S_IMODE(root.stat().st_mode)
            result = self.run_cli(
                "history",
                "import",
                "--source",
                "claude-code",
                "--root",
                str(root),
                "--output",
                str(root / "history.jsonl"),
                "--yes",
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            after = stat.S_IMODE(root.stat().st_mode)
            self.assertEqual(before, after)

    def test_history_import_saves_pending_checkpoint_without_empty_event_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            project_dir.mkdir(parents=True)
            record = {
                "type": "assistant",
                "sessionId": "sess-1",
                "timestamp": "2026-08-20T09:00:00Z",
                "cwd": str(root),
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Bash",
                            "input": {"command": "uv run python -m unittest"},
                        }
                    ],
                },
            }
            transcript = project_dir / "sess.jsonl"
            transcript.write_text(json.dumps(record) + "\n", encoding="utf-8")

            output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
            result = self.run_cli(
                "history", "import", "--source", "claude-code",
                "--root", str(root), "--yes",
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(output.exists())
            checkpoint_path = root / ".loopmetry" / "checkpoints" / "claude-code-history.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            position = next(iter(checkpoint["positions"].values()))
            self.assertIn("tool-1", position["pending"])

            result_record = {
                "type": "user",
                "sessionId": "sess-1",
                "timestamp": "2026-08-20T09:00:01Z",
                "cwd": str(root),
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tool-1", "is_error": False}
                    ],
                },
            }
            with transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result_record) + "\n")
            second = self.run_cli(
                "history", "import", "--source", "claude-code",
                "--root", str(root), "--yes",
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )

            self.assertEqual(second.returncode, 0, second.stderr)
            events = load_jsonl(output)
            self.assertEqual(len(events), 2)
            command = next(event for event in events if event.type.value == "command")
            self.assertEqual(command.data["status"], "success")


class IntegrateTests(unittest.TestCase):
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

    def _settings_path(self, root: Path) -> Path:
        return root / ".claude" / "settings.local.json"

    def test_preview_on_missing_file_shows_diff_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_cli("integrate", "claude-code", "--root", str(root), "--preview")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("UserPromptSubmit", result.stdout)
            self.assertFalse(self._settings_path(root).exists())

    def test_apply_on_missing_file_creates_it_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_cli("integrate", "claude-code", "--root", str(root), "--apply")
            self.assertEqual(result.returncode, 0, result.stderr)
            path = self._settings_path(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("UserPromptSubmit", data["hooks"])
            self.assertFalse(path.with_name(path.name + ".bak").exists())

    def test_reapply_is_noop_and_creates_no_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_cli("integrate", "claude-code", "--root", str(root), "--apply")
            second = self.run_cli("integrate", "claude-code", "--root", str(root), "--apply")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("no changes needed", second.stdout)
            path = self._settings_path(root)
            self.assertFalse(path.with_name(path.name + ".bak").exists())

    def test_apply_on_existing_unrelated_file_without_force_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._settings_path(root)
            path.parent.mkdir(parents=True)
            original = json.dumps({"otherSetting": True})
            path.write_text(original, encoding="utf-8")
            result = self.run_cli("integrate", "claude-code", "--root", str(root), "--apply")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_apply_with_force_merges_and_backs_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._settings_path(root)
            path.parent.mkdir(parents=True)
            original = json.dumps({"otherSetting": True})
            path.write_text(original, encoding="utf-8")
            result = self.run_cli(
                "integrate", "claude-code", "--root", str(root), "--apply", "--force"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(data["otherSetting"])
            self.assertIn("UserPromptSubmit", data["hooks"])
            backup = path.with_name(path.name + ".bak")
            self.assertEqual(backup.read_text(encoding="utf-8"), original)

    def test_corrupt_existing_json_fails_closed_for_all_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._settings_path(root)
            path.parent.mkdir(parents=True)
            path.write_text("{not valid json\n", encoding="utf-8")
            original = path.read_text(encoding="utf-8")
            for mode in ("--preview", "--apply", "--remove"):
                result = self.run_cli(
                    "integrate", "claude-code", "--root", str(root), mode, "--force"
                )
                self.assertEqual(result.returncode, 2, mode)
                self.assertEqual(path.read_text(encoding="utf-8"), original)
                self.assertFalse(path.with_name(path.name + ".bak").exists())

    def test_remove_after_apply_strips_only_managed_blocks_and_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_cli("integrate", "claude-code", "--root", str(root), "--apply")
            without_force = self.run_cli(
                "integrate", "claude-code", "--root", str(root), "--remove"
            )
            self.assertEqual(without_force.returncode, 2)

            with_force = self.run_cli(
                "integrate", "claude-code", "--root", str(root), "--remove", "--force"
            )
            self.assertEqual(with_force.returncode, 0, with_force.stderr)
            path = self._settings_path(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data, {})
            backup = path.with_name(path.name + ".bak")
            self.assertTrue(backup.exists())

    def test_remove_with_nothing_managed_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._settings_path(root)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"otherSetting": True}), encoding="utf-8")
            result = self.run_cli("integrate", "claude-code", "--root", str(root), "--remove")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no changes needed", result.stdout)

    def test_mutually_exclusive_modes_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_cli(
                "integrate", "claude-code", "--root", str(root), "--preview", "--apply"
            )
            self.assertEqual(result.returncode, 2)

    def test_source_codex_not_yet_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self.run_cli("integrate", "codex", "--root", str(root), "--preview")
            self.assertEqual(result.returncode, 2)

    def test_changing_project_id_replaces_hook_instead_of_adding_a_second_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_cli("integrate", "claude-code", "--root", str(root), "--apply")
            result = self.run_cli(
                "integrate",
                "claude-code",
                "--root",
                str(root),
                "--apply",
                "--project-id",
                "course-2026",
                "--force",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(self._settings_path(root).read_text(encoding="utf-8"))
            for event in ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure"):
                blocks = data["hooks"][event]
                self.assertEqual(len(blocks), 1)
                self.assertEqual(blocks[0]["hooks"][0]["args"][-2:], ["--project-id", "course-2026"])

    def test_malformed_nested_hooks_structure_fails_closed_without_partial_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._settings_path(root)
            path.parent.mkdir(parents=True)
            original = json.dumps({"hooks": {"PostToolUse": "invalid"}})
            path.write_text(original, encoding="utf-8")
            result = self.run_cli(
                "integrate", "claude-code", "--root", str(root), "--apply", "--force"
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertFalse(path.with_name(path.name + ".bak").exists())

    def test_remove_preserves_user_hook_outside_installer_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self._settings_path(root)
            path.parent.mkdir(parents=True)
            user_hook = {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "loopmetry",
                                    "args": [
                                        "capture-hook",
                                        "--source",
                                        "claude-code",
                                        "--output",
                                        "custom.jsonl",
                                    ],
                                }
                            ]
                        }
                    ]
                }
            }
            path.write_text(json.dumps(user_hook), encoding="utf-8")
            result = self.run_cli(
                "integrate", "claude-code", "--root", str(root), "--remove", "--force"
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("no changes needed", result.stdout)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), user_hook)


class HistoryConsentTests(unittest.TestCase):
    """In-process tests for behavior mock.patch can observe (call counts)."""

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

    def test_import_without_yes_never_reads_transcripts_when_not_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            with (
                mock.patch.object(
                    ClaudeCodeHistoryAdapter, "discover", autospec=True
                ) as mock_discover,
                mock.patch("sys.stdin.isatty", return_value=False),
                mock.patch.dict(os.environ, {"LOOPMETRY_CLAUDE_HOME": str(claude_home)}),
            ):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        main(
                            [
                                "history", "import", "--source", "claude-code",
                                "--root", str(root),
                            ]
                        )
                self.assertEqual(ctx.exception.code, 2)
            mock_discover.assert_not_called()


class RunAutoSourceTests(unittest.TestCase):
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

    def test_default_run_is_unaffected_by_new_flags_being_absent(self) -> None:
        source = str(ROOT / "examples" / "demo_project.jsonl")
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "runs"
            result = self.run_cli(
                "run", "--input", source,
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            run_dir = next(output_root.iterdir())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("source_coverage", manifest)
            self.assertNotIn("source diagnostics", result.stdout)

    def test_auto_non_interactive_without_include_history_skips_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            project_id = derive_project_id(str(root))
            (hooks / "claude-code.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "event_id": "evt-hook-1",
                        "project_id": project_id,
                        "session_id": "sess-1",
                        "timestamp": "2026-08-20T09:00:00Z",
                        "type": "project_start",
                        "actor": "human",
                        "source": "claude-code",
                        "data": {"summary": "Start project"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_root = root / "runs"
            result = self.run_cli(
                "run", "--source", "auto", "--root", str(root),
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / ".loopmetry" / "events" / "claude-code-history.jsonl").exists())

    def test_auto_non_interactive_with_include_history_imports_and_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            project_id = derive_project_id(str(root))
            (hooks / "claude-code.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "event_id": "evt-hook-1",
                        "project_id": project_id,
                        "session_id": "sess-1",
                        "timestamp": "2026-08-20T09:00:00Z",
                        "type": "project_start",
                        "actor": "human",
                        "source": "claude-code",
                        "data": {"summary": "Start project"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_root = root / "runs"
            result = self.run_cli(
                "run", "--source", "auto", "--root", str(root), "--include-history",
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            history_output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
            self.assertTrue(history_output.exists())
            run_dir = next(output_root.iterdir())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_coverage"]["mode"], "auto")
            self.assertTrue(manifest["source_coverage"]["history_included"])

    def test_auto_conflict_between_hook_and_history_is_reported_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            # A conflicting duplicate: same event_id as whatever the history
            # adapter derives for this session's first note event, but this
            # slice doesn't need to predict that ID -- instead, prove the
            # tolerant path activates end-to-end by pre-seeding the history
            # output file with a record that conflicts with a hook event that
            # shares its event_id.
            conflicting_id = "manual-conflict-1"
            (hooks / "claude-code.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "event_id": conflicting_id,
                        "project_id": "demo-expense-cli",
                        "session_id": "sess-1",
                        "timestamp": "2026-08-20T09:00:00Z",
                        "type": "note",
                        "actor": "system",
                        "source": "claude-code",
                        "data": {"summary": "from-hook"},
                        "provenance": [
                            {
                                "source": "claude-code",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events_dir = root / ".loopmetry" / "events"
            events_dir.mkdir(parents=True)
            (events_dir / "preexisting-history.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "event_id": conflicting_id,
                        "project_id": "demo-expense-cli",
                        "session_id": "sess-1",
                        "timestamp": "2026-08-20T09:00:00Z",
                        "type": "note",
                        "actor": "system",
                        "source": "claude-code",
                        "data": {"summary": "from-history"},
                        "provenance": [
                            {
                                "source": "claude-code",
                                "capture_mode": "history-backfill",
                                "adapter_version": "1.0.0",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_root = root / "runs"
            result = self.run_cli(
                "run", "--source", "auto", "--root", str(root),
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("source diagnostics: adapter_conflict=1", result.stdout)
            run_dir = next(output_root.iterdir())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_coverage"]["diagnostics"][0]["kind"], "adapter_conflict")

    def test_auto_interactive_prompts_are_reused_from_history_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            project_id = derive_project_id(str(root))
            (hooks / "claude-code.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "event_id": "evt-hook-1",
                        "project_id": project_id,
                        "session_id": "sess-1",
                        "timestamp": "2026-08-20T09:00:00Z",
                        "type": "project_start",
                        "actor": "human",
                        "source": "claude-code",
                        "data": {"summary": "Start project"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_root = root / "runs"
            with (
                mock.patch("builtins.input", side_effect=["y", "y"]) as mock_input,
                mock.patch("sys.stdin.isatty", return_value=True),
                mock.patch.dict(os.environ, {"LOOPMETRY_CLAUDE_HOME": str(claude_home)}),
            ):
                exit_code = main(
                    [
                        "run", "--source", "auto", "--root", str(root),
                        "--assignment-id", "course-2026", "--submitter-id", "S001",
                        "--output-root", str(output_root),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(mock_input.call_count, 2)
            self.assertTrue((root / ".loopmetry" / "events" / "claude-code-history.jsonl").exists())

    def test_since_and_until_are_passed_to_history_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            project_id = derive_project_id(str(root))
            (hooks / "claude-code.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "event_id": "evt-hook-1",
                        "project_id": project_id,
                        "session_id": "sess-1",
                        "timestamp": "2026-08-20T09:00:00Z",
                        "type": "project_start",
                        "actor": "human",
                        "source": "claude-code",
                        "data": {"summary": "Start project"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_root = root / "runs"
            with (
                mock.patch.object(
                    ClaudeCodeHistoryAdapter, "discover", autospec=True, return_value=()
                ) as mock_discover,
                mock.patch.dict(os.environ, {"LOOPMETRY_CLAUDE_HOME": str(claude_home)}),
            ):
                exit_code = main(
                    [
                        "run", "--source", "auto", "--root", str(root), "--include-history",
                        "--since", "2026-08-01", "--until", "2026-08-31",
                        "--assignment-id", "course-2026", "--submitter-id", "S001",
                        "--output-root", str(output_root),
                    ]
                )
            self.assertEqual(exit_code, 0)
            mock_discover.assert_called_once()
            context = mock_discover.call_args.args[1]
            self.assertEqual(context.since.strftime("%Y-%m-%d"), "2026-08-01")
            self.assertEqual(context.until.strftime("%Y-%m-%d"), "2026-08-31")

    def test_until_date_event_is_included_and_since_after_until_is_rejected(self) -> None:
        # --until 2026-08-20 must include events timestamped on 2026-08-20
        # itself (an "upper bound" reading of "through that day"), not
        # silently exclude the whole day by parsing to its midnight.
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            output_root = root / "runs"
            result = self.run_cli(
                "run", "--source", "auto", "--root", str(root), "--include-history",
                "--since", "2026-08-20", "--until", "2026-08-20",
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            history_output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
            self.assertTrue(history_output.exists())
            events = load_jsonl(history_output)
            self.assertEqual(len(events), 1)

            reversed_result = self.run_cli(
                "run", "--source", "auto", "--root", str(root), "--include-history",
                "--since", "2026-08-21", "--until", "2026-08-20",
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(root / "runs-reversed"),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(reversed_result.returncode, 2)
            self.assertIn("--since", reversed_result.stderr)

    def test_auto_include_history_with_no_matching_history_then_plain_run_still_succeeds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            # An empty claude_home (no project directory at all) means
            # discover() finds zero candidates for this project -- the
            # "no matching local Claude Code history" case.
            claude_home = Path(tmp) / "claude-home"
            claude_home.mkdir(parents=True)
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples" / "demo_project.jsonl", hooks / "claude-code.jsonl")

            output_root = root / "runs"
            env = {**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)}
            auto_result = self.run_cli(
                "run", "--source", "auto", "--root", str(root), "--include-history",
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
                env=env,
            )
            self.assertEqual(auto_result.returncode, 0, auto_result.stderr)
            history_output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
            self.assertFalse(
                history_output.exists(),
                "a zero-event history import must not create an empty output file",
            )

            plain_result = self.run_cli(
                "run", "--root", str(root),
                "--assignment-id", "course-2026", "--submitter-id", "S002",
                "--output-root", str(root / "runs-plain"),
                env=env,
            )
            self.assertEqual(plain_result.returncode, 0, plain_result.stderr)


if __name__ == "__main__":
    unittest.main()
