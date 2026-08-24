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

    def test_since_filters_by_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, project_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_session(project_dir, "a.jsonl", [_record("user", cwd=root)])
            future = DiscoveryContext(
                project_root=context.project_root,
                since=datetime(2999, 1, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(adapter.discover(future), ())


if __name__ == "__main__":
    unittest.main()
