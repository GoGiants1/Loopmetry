from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from loopmetry.submission import load_submission
from loopmetry.workflow import (
    discover_event_files,
    load_event_files,
    run_participant_workflow,
)


ROOT = Path(__file__).resolve().parents[1]


class ParticipantWorkflowTests(unittest.TestCase):
    def test_one_command_local_artifacts(self) -> None:
        source = ROOT / "examples" / "demo_project.jsonl"
        with tempfile.TemporaryDirectory() as directory:
            artifacts = run_participant_workflow(
                [source],
                assignment_id="course-2026",
                submitter_id="S001",
                output_root=Path(directory) / "runs",
            )
            self.assertTrue(artifacts.report_json.is_file())
            self.assertTrue(artifacts.report_html.is_file())
            self.assertTrue(artifacts.submission_json.is_file())
            self.assertTrue(artifacts.manifest_json.is_file())
            self.assertIsNone(artifacts.receipt)
            envelope = load_submission(artifacts.submission_json)
            self.assertEqual(envelope["assignment_id"], "course-2026")
            self.assertEqual(envelope["submitter_id"], "S001")
            self.assertNotIn("events", envelope)
            manifest = json.loads(artifacts.manifest_json.read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["source_files"]), 1)

    def test_capture_discovery_and_identical_deduplication(self) -> None:
        source = ROOT / "examples" / "demo_project.jsonl"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            first = hooks / "claude-code.jsonl"
            second = hooks / "codex.jsonl"
            shutil.copyfile(source, first)
            shutil.copyfile(source, second)

            discovered = discover_event_files(root)
            self.assertEqual(discovered, [first, second])
            events = load_event_files(discovered)
            self.assertEqual(len(events), 20)


if __name__ == "__main__":
    unittest.main()
