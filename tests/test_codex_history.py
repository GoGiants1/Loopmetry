"""Tests for the Codex historical-backfill adapter (synthetic rollout files only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loopmetry.adapters.base import Coverage, DiscoveryContext
from loopmetry.adapters.base import DiscoveryContext as _DC
from loopmetry.adapters.codex_history import CodexHistoryAdapter


def _session_meta(cwd: str, session_id: str = "sess-0001", **extra: object) -> dict:
    payload = {
        "session_id": session_id,
        "id": session_id,
        "timestamp": "2026-08-20T09:00:00Z",
        "cwd": cwd,
        "originator": "codex_cli_rs",
        "cli_version": "0.130.0",
    }
    payload.update(extra)
    return {"timestamp": "2026-08-20T09:00:00Z", "type": "session_meta", "payload": payload}


def _write_rollout(sessions_dir: Path, name: str, records: list[dict]) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


class DiscoveryTests(unittest.TestCase):
    def _setup(self, tmp: str) -> tuple[CodexHistoryAdapter, DiscoveryContext, Path]:
        root = Path(tmp) / "work" / "project"
        root.mkdir(parents=True)
        codex_home = Path(tmp) / "codex-home"
        sessions_dir = codex_home / "sessions" / "2026" / "08" / "20"
        adapter = CodexHistoryAdapter(codex_home=codex_home)
        context = DiscoveryContext(project_root=root)
        return adapter, context, sessions_dir

    def test_no_codex_home_discovers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, _ = self._setup(tmp)
            self.assertEqual(adapter.discover(context), ())

    def test_discovers_sessions_whose_cwd_matches_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, sessions_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_rollout(sessions_dir, "rollout-b.jsonl", [_session_meta(root)])
            _write_rollout(sessions_dir, "rollout-a.jsonl", [_session_meta(root)])
            _write_rollout(
                sessions_dir, "rollout-other.jsonl", [_session_meta("/somewhere/else")]
            )
            candidates = adapter.discover(context)
            self.assertEqual(
                sorted(c.label for c in candidates), ["rollout-a.jsonl", "rollout-b.jsonl"]
            )
            self.assertEqual(candidates[0].source, "codex-history")
            self.assertEqual(candidates[0].session_id, "sess-0001")
            kinds = [d.kind for d in adapter.last_discovery_diagnostics]
            self.assertIn("unattributed_session", kinds)

    def test_discovers_sessions_nested_under_date_subdirectories(self) -> None:
        # Confirmed path shape: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
        # (codex-rs/rollout/src/list.rs). Discovery must glob recursively rather
        # than assume this exact depth, since it's an implementation detail.
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, sessions_dir = self._setup(tmp)
            _write_rollout(
                sessions_dir, "rollout-a.jsonl", [_session_meta(str(context.project_root))]
            )
            self.assertEqual(len(adapter.discover(context)), 1)

    def test_missing_session_meta_is_unattributed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, sessions_dir = self._setup(tmp)
            _write_rollout(
                sessions_dir,
                "rollout-a.jsonl",
                [{"timestamp": "2026-08-20T09:00:00Z", "type": "turn_context", "payload": {}}],
            )
            self.assertEqual(adapter.discover(context), ())
            kinds = [d.kind for d in adapter.last_discovery_diagnostics]
            self.assertIn("unattributed_session", kinds)

    def test_discover_does_not_filter_by_file_mtime(self) -> None:
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, sessions_dir = self._setup(tmp)
            _write_rollout(
                sessions_dir, "rollout-a.jsonl", [_session_meta(str(context.project_root))]
            )
            future = DiscoveryContext(
                project_root=context.project_root,
                since=datetime(2999, 1, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(len(adapter.discover(future)), 1)


def _response_item(payload: dict, timestamp: str = "2026-08-20T09:01:00Z") -> dict:
    return {"timestamp": timestamp, "type": "response_item", "payload": payload}


def _user_message(text: str, timestamp: str = "2026-08-20T09:01:00Z") -> dict:
    return _response_item(
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        timestamp,
    )


def _local_shell_call(
    call_id: str, command: list[str], status: str = "completed", timestamp: str = "2026-08-20T09:01:05Z"
) -> dict:
    return _response_item(
        {
            "type": "local_shell_call",
            "call_id": call_id,
            "status": status,
            "action": {"type": "exec", "command": command},
        },
        timestamp,
    )


def _function_call(call_id: str, name: str, arguments: dict, timestamp: str = "2026-08-20T09:01:05Z") -> dict:
    return _response_item(
        {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments),
        },
        timestamp,
    )


def _function_call_output(call_id: str, output: str = "ok", timestamp: str = "2026-08-20T09:01:06Z") -> dict:
    return _response_item({"type": "function_call_output", "call_id": call_id, "output": output}, timestamp)


class ImportTests(unittest.TestCase):
    def _import(self, records: list[dict]):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            codex_home = Path(tmp) / "codex-home"
            sessions_dir = codex_home / "sessions" / "2026" / "08" / "20"
            full = [_session_meta(str(root))] + records
            _write_rollout(sessions_dir, "rollout-a.jsonl", full)
            adapter = CodexHistoryAdapter(codex_home=codex_home)
            context = _DC(project_root=root)
            candidates = adapter.discover(context)
            return adapter.import_candidates(candidates, context), context

    def test_user_message_becomes_human_intervention_event(self) -> None:
        run, _ = self._import([_user_message("do the thing")])
        self.assertEqual(len(run.events), 1)
        event = run.events[0]
        self.assertEqual(event.type.value, "human_intervention")
        self.assertEqual(event.source, "codex")
        self.assertNotIn("do the thing", json.dumps(event.data))

    def test_local_shell_call_completed_in_one_record_becomes_command_with_unknown_status(self) -> None:
        run, _ = self._import([_local_shell_call("call-1", ["bash", "-lc", "pytest"])])
        commands = [e for e in run.events if e.type.value == "command"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].data["status"], "unknown")
        self.assertEqual(commands[0].data["command"], "pytest")
        kinds = [d.kind for d in run.diagnostics]
        self.assertIn("command_status_unavailable", kinds)

    def test_apply_patch_call_becomes_file_change_event(self) -> None:
        patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch"
        run, _ = self._import([_local_shell_call("call-2", ["apply_patch", patch])])
        changes = [e for e in run.events if e.type.value == "file_change"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].data["path"], "src/app.py")

    def test_apply_patch_multi_file_patch_emits_one_event_per_file(self) -> None:
        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/app.py\n"
            "@@\n-old\n+new\n"
            "*** Update File: src/other.py\n"
            "@@\n-old2\n+new2\n"
            "*** End Patch"
        )
        run, _ = self._import([_local_shell_call("call-2b", ["apply_patch", patch])])
        changes = [e for e in run.events if e.type.value == "file_change"]
        self.assertEqual(len(changes), 2)
        self.assertEqual(
            sorted(c.data["path"] for c in changes), ["src/app.py", "src/other.py"]
        )

    def test_apply_patch_add_file_header_sets_add_action(self) -> None:
        patch = "*** Begin Patch\n*** Add File: src/new_file.py\n@@\n+new\n*** End Patch"
        run, _ = self._import([_local_shell_call("call-2c", ["apply_patch", patch])])
        changes = [e for e in run.events if e.type.value == "file_change"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].data["action"], "add")

    def test_apply_patch_traversal_path_is_sanitized(self) -> None:
        patch = "*** Begin Patch\n*** Update File: /etc/passwd\n@@\n-old\n+new\n*** End Patch"
        run, _ = self._import([_local_shell_call("call-2d", ["apply_patch", patch])])
        changes = [e for e in run.events if e.type.value == "file_change"]
        self.assertEqual(len(changes), 1)
        self.assertNotEqual(changes[0].data["path"], "/etc/passwd")
        self.assertTrue(changes[0].data["path"].startswith("<external-path-redacted>/"))

    def test_pending_apply_patch_checkpoint_never_contains_raw_patch_text(self) -> None:
        # Regression guard: an apply_patch call that is still in flight (no
        # matching completion record yet) must never have its raw patch body
        # written into the on-disk checkpoint JSON produced by position().
        # Only the pre-extracted, pre-sanitized (action, path) pairs may be
        # stashed in the pending map.
        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/app.py\n"
            "@@\n-old\n+ return 123\n"
            "*** End Patch"
        )
        run, _ = self._import(
            [_local_shell_call("call-6", ["apply_patch", patch], status="in_progress")]
        )
        self.assertIsNotNone(run.checkpoint)
        positions = run.checkpoint.positions
        pending = next(iter(positions.values()))["pending"]
        self.assertIn("call-6", pending)
        checkpoint_json = json.dumps(positions)
        self.assertNotIn("+ return 123", checkpoint_json)
        self.assertNotIn("-old", checkpoint_json)
        self.assertNotIn("Begin Patch", checkpoint_json)

    def test_function_call_and_output_pair_across_two_records(self) -> None:
        run, _ = self._import(
            [
                _function_call("call-3", "shell", {"command": ["bash", "-lc", "ruff check ."]}),
                _function_call_output("call-3", "0 errors"),
            ]
        )
        commands = [e for e in run.events if e.type.value == "command"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].data["status"], "unknown")

    def test_unresolved_call_is_pending_not_dropped(self) -> None:
        run, _ = self._import([_function_call("call-4", "shell", {"command": ["bash", "-lc", "sleep 1"]})])
        self.assertEqual([e for e in run.events if e.type.value == "command"], [])
        kinds = [d.kind for d in run.diagnostics]
        self.assertIn("unresolved_tool_call", kinds)
        self.assertIsNotNone(run.checkpoint)
        positions = run.checkpoint.positions
        pending = next(iter(positions.values()))["pending"]
        self.assertIn("call-4", pending)

    def test_unknown_response_item_type_is_diagnosed_not_dropped_silently(self) -> None:
        run, _ = self._import([_response_item({"type": "reasoning", "summary": []})])
        self.assertEqual(run.events, ())
        kinds = [d.kind for d in run.diagnostics]
        self.assertIn("skipped_record_type", kinds)

    def test_coverage_is_partial_when_any_command_is_emitted(self) -> None:
        run, _ = self._import([_local_shell_call("call-5", ["bash", "-lc", "pytest"])])
        self.assertEqual(run.coverage.categories["commands"], Coverage.PARTIAL)


if __name__ == "__main__":
    unittest.main()
