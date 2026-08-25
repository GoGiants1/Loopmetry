"""Tests for the Claude Code historical-backfill adapter (synthetic transcripts only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from loopmetry.adapters.base import DiscoveryContext
from loopmetry.adapters.claude_code_history import (
    ClaudeCodeHistoryAdapter,
    encode_claude_project_dir,
)


def _record(record_type: str, **extra: object) -> dict:
    base: dict = {
        "type": record_type,
        "sessionId": "sess-0001",
        "timestamp": "2026-08-20T09:00:00Z",
        "cwd": extra.pop("cwd", "/work/project"),
        "version": "2.0.0",
    }
    base.update(extra)
    return base


def _write_session(project_dir: Path, name: str, records: list[dict]) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


class EncodingTests(unittest.TestCase):
    def test_slashes_and_dots_become_dashes(self) -> None:
        self.assertEqual(
            encode_claude_project_dir(Path("/Users/w/my.app")), "-Users-w-my-app"
        )


class DiscoveryTests(unittest.TestCase):
    def _setup(self, tmp: str) -> tuple[ClaudeCodeHistoryAdapter, DiscoveryContext, Path]:
        root = Path(tmp) / "work" / "project"
        root.mkdir(parents=True)
        claude_home = Path(tmp) / "claude-home"
        project_dir = claude_home / "projects" / encode_claude_project_dir(root)
        adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
        context = DiscoveryContext(project_root=root)
        return adapter, context, project_dir

    def test_no_claude_home_discovers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, _ = self._setup(tmp)
            self.assertEqual(adapter.discover(context), ())

    def test_discovers_sessions_whose_cwd_matches_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, project_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_session(project_dir, "b.jsonl", [_record("user", cwd=root)])
            _write_session(project_dir, "a.jsonl", [_record("user", cwd=root)])
            _write_session(
                project_dir, "other.jsonl", [_record("user", cwd="/somewhere/else")]
            )
            candidates = adapter.discover(context)
            self.assertEqual([c.label for c in candidates], ["a.jsonl", "b.jsonl"])
            self.assertEqual(candidates[0].source, "claude-code-history")
            self.assertEqual(candidates[0].session_id, "sess-0001")
            kinds = [d.kind for d in adapter.last_discovery_diagnostics]
            self.assertIn("unattributed_session", kinds)

    def test_skips_queue_operation_lines_when_reading_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, project_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_session(
                project_dir,
                "a.jsonl",
                [{"type": "queue-operation"}, _record("user", cwd=root)],
            )
            self.assertEqual(len(adapter.discover(context)), 1)

    def test_discover_does_not_filter_by_file_mtime(self) -> None:
        # A session file is appended to for as long as it is active, so its mtime
        # (reflecting only the last append) cannot bound the range of event
        # timestamps inside it (D-013 note; same reasoning as HookSourceAdapter).
        # since/until must be enforced per-event in import_candidates(), not here.
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, project_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_session(project_dir, "a.jsonl", [_record("user", cwd=root)])
            future = DiscoveryContext(
                project_root=context.project_root,
                since=datetime(2999, 1, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(len(adapter.discover(future)), 1)


def _assistant_tool_use(name: str, tool_input: dict, tool_use_id: str, cwd: str) -> dict:
    return _record(
        "assistant",
        cwd=cwd,
        message={
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}
            ],
        },
    )


def _user_tool_result(tool_use_id: str, cwd: str, is_error: bool = False) -> dict:
    return _record(
        "user",
        cwd=cwd,
        message={
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": is_error}
            ],
        },
    )


class ImportTests(unittest.TestCase):
    def _import(self, records: list[dict]):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            for record in records:
                record["cwd"] = str(root)
            _write_session(project_dir, "sess.jsonl", records)
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            return adapter.import_candidates(adapter.discover(context), context)

    def test_prompt_becomes_hashed_human_intervention(self) -> None:
        run = self._import(
            [_record("user", message={"role": "user", "content": "please fix the bug"})]
        )
        events = [e for e in run.events if e.type.value == "human_intervention"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("please fix the bug", json.dumps(events[0].to_mapping()))
        self.assertEqual(events[0].data["prompt_length"], len("please fix the bug"))
        record = events[0].provenance[0]
        self.assertEqual(record.capture_mode.value, "history-backfill")
        self.assertEqual(record.source_ref["session_file"], "sess.jsonl")

    def test_read_and_edit_become_file_events_with_relative_paths(self) -> None:
        cwd_marker = "__CWD__"
        records = [
            _assistant_tool_use("Read", {"file_path": cwd_marker + "/src/a.py"}, "t1", ""),
            _assistant_tool_use("Edit", {"file_path": cwd_marker + "/src/a.py"}, "t2", ""),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            for record in records:
                record["cwd"] = str(root)
                block = record["message"]["content"][0]
                block["input"]["file_path"] = block["input"]["file_path"].replace(
                    cwd_marker, str(root)
                )
            _write_session(project_dir, "sess.jsonl", records)
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
        types = sorted(e.type.value for e in run.events)
        self.assertEqual(types, ["file_change", "file_read"])
        for event in run.events:
            self.assertEqual(event.data["path"], "src/a.py")

    def test_bash_test_command_yields_command_verification_and_error(self) -> None:
        run = self._import(
            [
                _assistant_tool_use(
                    "Bash", {"command": "uv run python -m unittest"}, "t1", ""
                ),
                _user_tool_result("t1", "", is_error=True),
            ]
        )
        by_type = {e.type.value: e for e in run.events}
        self.assertEqual(by_type["command"].data["status"], "failed")
        self.assertNotIn("uv run", json.dumps(by_type["command"].to_mapping()["data"]))
        self.assertEqual(by_type["verification"].data["status"], "failed")
        self.assertIn("error", by_type)

    def test_unknown_record_types_become_diagnostics_not_events(self) -> None:
        run = self._import(
            [
                _record("file-history-snapshot"),
                _record("user", message={"role": "user", "content": "hi"}),
            ]
        )
        kinds = {d.kind for d in run.diagnostics}
        self.assertIn("skipped_record_type", kinds)
        self.assertEqual(len(run.events), 1)

    def test_reimport_is_deterministic(self) -> None:
        # Re-importing the same transcript (no checkpoint passed either time, so
        # everything is re-read from scratch) must yield byte-identical events.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            record = _record(
                "user", cwd=str(root), message={"role": "user", "content": "hi"}
            )
            _write_session(project_dir, "sess.jsonl", [record])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            first = adapter.import_candidates(adapter.discover(context), context)
            second = adapter.import_candidates(adapter.discover(context), context)
        self.assertEqual(
            [e.to_mapping() for e in first.events],
            [e.to_mapping() for e in second.events],
        )

    def test_import_candidates_filters_by_event_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            record = _record(
                "user",
                cwd=str(root),
                timestamp="2020-01-01T00:00:00Z",
                message={"role": "user", "content": "too old"},
            )
            _write_session(project_dir, "sess.jsonl", [record])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(
                project_root=root,
                since=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
            run = adapter.import_candidates(adapter.discover(context), context)
        self.assertEqual(run.events, ())

    def test_late_append_does_not_exclude_earlier_events(self) -> None:
        # A single file's mtime reflects its last append; an `until` bound between
        # an early and a late event must exclude only the late one, not the whole
        # file (which discover()'s now-unconditional listing makes possible).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            early = _record(
                "user",
                cwd=str(root),
                timestamp="2026-01-01T00:00:00Z",
                message={"role": "user", "content": "early"},
            )
            late = _record(
                "user",
                cwd=str(root),
                timestamp="2026-06-01T00:00:00Z",
                message={"role": "user", "content": "late"},
            )
            _write_session(project_dir, "sess.jsonl", [early, late])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(
                project_root=root,
                until=datetime(2026, 3, 1, tzinfo=timezone.utc),
            )
            run = adapter.import_candidates(adapter.discover(context), context)
        self.assertEqual(len(run.events), 1)
        self.assertEqual(run.events[0].timestamp.isoformat(), "2026-01-01T00:00:00+00:00")

    def test_widening_since_after_narrow_import_recovers_earlier_events(self) -> None:
        # A narrow first import must not permanently strand the out-of-window
        # record behind the checkpoint's records_read cursor: re-importing with
        # an earlier --since must still be able to recover it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            old = _record(
                "user",
                cwd=str(root),
                timestamp="2026-08-01T00:00:00Z",
                message={"role": "user", "content": "old"},
            )
            recent = _record(
                "user",
                cwd=str(root),
                timestamp="2026-08-20T00:00:00Z",
                message={"role": "user", "content": "recent"},
            )
            _write_session(project_dir, "sess.jsonl", [old, recent])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            narrow = DiscoveryContext(
                project_root=root, since=datetime(2026, 8, 10, tzinfo=timezone.utc)
            )
            first = adapter.import_candidates(adapter.discover(narrow), narrow)
            self.assertEqual(len(first.events), 1)
            self.assertEqual(
                first.events[0].timestamp.isoformat(), "2026-08-20T00:00:00+00:00"
            )

            wide = DiscoveryContext(
                project_root=root, since=datetime(2026, 1, 1, tzinfo=timezone.utc)
            )
            second = adapter.import_candidates(
                adapter.discover(wide), wide, checkpoint=first.checkpoint
            )
            timestamps = sorted(e.timestamp.isoformat() for e in second.events)
            self.assertEqual(
                timestamps, ["2026-08-01T00:00:00+00:00", "2026-08-20T00:00:00+00:00"]
            )
            self.assertIn("window_widened", {d.kind for d in second.diagnostics})


class IncrementalImportTests(unittest.TestCase):
    def test_second_import_with_checkpoint_only_reads_new_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            first_record = _record(
                "user", cwd=str(root), message={"role": "user", "content": "one"}
            )
            path = _write_session(project_dir, "sess.jsonl", [first_record])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            run_one = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(len(run_one.events), 1)

            second_record = _record(
                "user",
                cwd=str(root),
                timestamp="2026-08-20T10:00:00Z",
                message={"role": "user", "content": "two"},
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(second_record) + "\n")
            run_two = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_one.checkpoint
            )
            self.assertEqual(len(run_two.events), 1)
            self.assertNotEqual(
                run_one.events[0].event_id, run_two.events[0].event_id
            )

    def test_pending_bash_call_resolved_by_later_append_yields_one_correct_event(self) -> None:
        """D-013: a tool_use at the checkpoint boundary must not lose its real result."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            tool_use = _assistant_tool_use(
                "Bash", {"command": "uv run pytest"}, "t1", ""
            )
            tool_use["cwd"] = str(root)
            path = _write_session(project_dir, "sess.jsonl", [tool_use])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)

            run_one = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual([e.type.value for e in run_one.events], [])
            self.assertIn("unresolved_tool_call", {d.kind for d in run_one.diagnostics})

            result = _user_tool_result("t1", "", is_error=False)
            result["cwd"] = str(root)
            result["timestamp"] = "2026-08-20T09:00:01Z"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result) + "\n")
            run_two = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_one.checkpoint
            )
            by_type = {e.type.value: e for e in run_two.events}
            self.assertEqual(by_type["command"].data["status"], "success")
            self.assertNotIn("stalled_tool_call", {d.kind for d in run_two.diagnostics})

    def test_stalled_bash_call_finalizes_to_unknown_only_after_no_growth(self) -> None:
        """D-013: only finalize to unknown once an import cycle shows the file stopped growing."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            tool_use = _assistant_tool_use(
                "Bash", {"command": "uv run pytest"}, "t1", ""
            )
            tool_use["cwd"] = str(root)
            _write_session(project_dir, "sess.jsonl", [tool_use])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)

            run_one = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual([e.type.value for e in run_one.events], [])

            # Nothing appended: the file has not grown since run_one left this entry pending.
            run_two = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_one.checkpoint
            )
            by_type = {e.type.value: e for e in run_two.events}
            self.assertEqual(by_type["command"].data["status"], "unknown")
            self.assertIn("stalled_tool_call", {d.kind for d in run_two.diagnostics})

            # A third import must not re-emit or conflict with the now-finalized event.
            run_three = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_two.checkpoint
            )
            self.assertEqual(run_three.events, ())

    def test_rotated_transcript_resets_checkpoint_with_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            record = _record(
                "user", cwd=str(root), message={"role": "user", "content": "one"}
            )
            _write_session(project_dir, "sess.jsonl", [record])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            run_one = adapter.import_candidates(adapter.discover(context), context)

            replacement = _record(
                "user",
                cwd=str(root),
                timestamp="2026-08-21T09:00:00Z",
                message={"role": "user", "content": "different first line"},
            )
            _write_session(project_dir, "sess.jsonl", [replacement])
            run_two = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_one.checkpoint
            )
            self.assertEqual(len(run_two.events), 1)
            self.assertIn("checkpoint_reset", {d.kind for d in run_two.diagnostics})


if __name__ == "__main__":
    unittest.main()
