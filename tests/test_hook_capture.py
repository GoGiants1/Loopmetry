from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from loopmetry.hook_capture import append_events, normalize_hook_payload
from loopmetry.schema import EventType


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


class HookCaptureTests(unittest.TestCase):
    def test_claude_write_emits_safe_file_change(self) -> None:
        payload = {
            "session_id": "session-1",
            "cwd": "/work/private/project",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_use_id": "tool-1",
            "tool_input": {
                "file_path": "/work/private/project/src/app.py",
                "content": "SECRET_SOURCE_CODE",
            },
            "tool_response": {"message": "written"},
        }
        events = normalize_hook_payload(
            payload,
            source="claude-code",
            project_id="demo",
            captured_at=NOW,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.FILE_CHANGE)
        self.assertEqual(events[0].data["path"], "src/app.py")
        serialized = json.dumps([event.to_mapping() for event in events])
        self.assertNotIn("SECRET_SOURCE_CODE", serialized)
        self.assertNotIn("/work/private", serialized)

    def test_codex_test_command_is_hashed_and_classified(self) -> None:
        payload = {
            "session_id": "session-2",
            "cwd": "/repo",
            "hook_event_name": "PostToolUse",
            "tool_name": "exec_command",
            "tool_use_id": "tool-2",
            "tool_input": {"command": "pytest -q --token super-secret"},
            "tool_response": {"exit_code": 0, "output": "12 passed"},
        }
        events = normalize_hook_payload(
            payload,
            source="codex",
            project_id="demo",
            captured_at=NOW,
        )
        self.assertEqual([event.type for event in events], [EventType.COMMAND, EventType.VERIFICATION])
        self.assertEqual(events[0].data["command"], "pytest")
        self.assertEqual(events[0].data["status"], "success")
        self.assertEqual(events[1].data["status"], "passed")
        serialized = json.dumps([event.to_mapping() for event in events])
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("12 passed", serialized)

    def test_nested_codex_response_status_is_detected(self) -> None:
        payload = {
            "session_id": "session-nested",
            "cwd": "/repo",
            "hook_event_name": "PostToolUse",
            "tool_name": "exec_command",
            "tool_use_id": "tool-nested",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {
                "content": [{"text": "Process exited with code 0"}],
            },
        }
        events = normalize_hook_payload(
            payload,
            source="codex",
            project_id="demo",
            captured_at=NOW,
        )
        self.assertEqual(events[0].data["status"], "success")
        self.assertEqual(events[1].data["status"], "passed")

    def test_windows_paths_are_relative_or_redacted_cross_platform(self) -> None:
        inside_payload = {
            "session_id": "session-win",
            "cwd": r"C:\work\repo",
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_use_id": "tool-win-1",
            "tool_input": {"file_path": r"C:\work\repo\src\app.py", "content": "secret"},
            "tool_response": {"success": True},
        }
        inside = normalize_hook_payload(
            inside_payload,
            source="claude-code",
            project_id="demo",
            captured_at=NOW,
        )
        self.assertEqual(inside[0].data["path"], "src/app.py")

        outside_payload = {
            **inside_payload,
            "tool_use_id": "tool-win-2",
            "tool_input": {"file_path": r"D:\private\secrets.txt", "content": "secret"},
        }
        outside = normalize_hook_payload(
            outside_payload,
            source="claude-code",
            project_id="demo",
            captured_at=NOW,
        )
        self.assertEqual(outside[0].data["path"], "<external-path-redacted>/secrets.txt")
        self.assertNotIn("D:", json.dumps(outside[0].to_mapping()))

    def test_task_completed_is_recorded_without_task_text(self) -> None:
        payload = {
            "session_id": "session-task",
            "cwd": "/repo",
            "hook_event_name": "TaskCompleted",
            "task_subject": "Customer-specific classified task",
        }
        events = normalize_hook_payload(
            payload,
            source="claude-code",
            project_id="demo",
            captured_at=NOW,
        )
        self.assertEqual(events[0].type, EventType.NOTE)
        self.assertEqual(events[0].data["hook_event"], "TaskCompleted")
        self.assertNotIn("classified", json.dumps(events[0].to_mapping()).lower())

    def test_prompt_content_is_not_retained(self) -> None:
        payload = {
            "session_id": "session-3",
            "cwd": "/repo",
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Implement customer project ORANGE-CLASSIFIED",
        }
        events = normalize_hook_payload(
            payload,
            source="claude-code",
            project_id="demo",
            captured_at=NOW,
        )
        self.assertEqual(events[0].type, EventType.HUMAN_INTERVENTION)
        self.assertEqual(events[0].data["prompt_length"], len(payload["prompt"]))
        serialized = json.dumps(events[0].to_mapping())
        self.assertNotIn("ORANGE-CLASSIFIED", serialized)

    def test_apply_patch_extracts_paths_without_patch_body(self) -> None:
        patch = "*** Begin Patch\n*** Update File: src/core.py\n@@\n-old secret\n+new secret\n*** End Patch"
        payload = {
            "session_id": "session-4",
            "cwd": "/repo",
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "tool_response": {"success": True},
        }
        events = normalize_hook_payload(
            payload,
            source="codex",
            project_id="demo",
            captured_at=NOW,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].data, {"path": "src/core.py", "action": "modify"})
        self.assertNotIn("new secret", json.dumps(events[0].to_mapping()))

    def test_append_events_uses_jsonl(self) -> None:
        payload = {
            "session_id": "session-5",
            "cwd": "/repo",
            "hook_event_name": "SessionEnd",
            "reason": "other",
        }
        events = normalize_hook_payload(
            payload,
            source="codex",
            project_id="demo",
            captured_at=NOW,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.jsonl"
            self.assertEqual(append_events(output, events), 1)
            line = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(line["project_id"], "demo")

    def test_short_append_fails_loudly(self) -> None:
        payload = {
            "session_id": "session-short-write",
            "cwd": "/repo",
            "hook_event_name": "SessionEnd",
            "reason": "other",
        }
        events = normalize_hook_payload(
            payload,
            source="codex",
            project_id="demo",
            captured_at=NOW,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "capture.jsonl"
            with patch("loopmetry.hook_capture.os.write", return_value=1):
                with self.assertRaises(OSError):
                    append_events(output, events)


if __name__ == "__main__":
    unittest.main()
