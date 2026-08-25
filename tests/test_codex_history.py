"""Tests for the Codex historical-backfill adapter (synthetic rollout files only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loopmetry.adapters.base import DiscoveryContext
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


if __name__ == "__main__":
    unittest.main()
