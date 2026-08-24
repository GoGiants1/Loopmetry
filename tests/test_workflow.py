from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from loopmetry.io import InputError
from loopmetry.submission import load_submission
from loopmetry.workflow import (
    discover_event_files,
    load_event_files,
    run_participant_workflow,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _base_event(**overrides: object) -> dict:
    event = {
        "schema_version": "0.2",
        "event_id": "evt-1",
        "project_id": "proj",
        "session_id": "sess",
        "timestamp": "2026-08-23T10:00:00Z",
        "type": "note",
        "actor": "system",
        "source": "claude-code",
        "data": {"summary": "x"},
        "provenance": [],
    }
    event.update(overrides)
    return event


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

    def test_provenance_only_difference_merges_instead_of_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            _write_jsonl(
                first,
                [
                    _base_event(
                        provenance=[
                            {
                                "source": "claude-code",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ]
                    )
                ],
            )
            _write_jsonl(second, [_base_event(provenance=[])])

            events = load_event_files([first, second])
            self.assertEqual(len(events), 1)
            self.assertEqual(len(events[0].provenance), 1)

    def test_two_different_provenance_records_merge_without_duplication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            _write_jsonl(
                first,
                [
                    _base_event(
                        provenance=[
                            {
                                "source": "claude-code",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ]
                    )
                ],
            )
            _write_jsonl(
                second,
                [
                    _base_event(
                        provenance=[
                            {
                                "source": "codex",
                                "capture_mode": "hook",
                                "adapter_version": "2.0.0",
                            }
                        ]
                    )
                ],
            )

            events = load_event_files([first, second])
            self.assertEqual(len(events), 1)
            self.assertEqual(len(events[0].provenance), 2)
            sources = sorted(record.source for record in events[0].provenance)
            self.assertEqual(sources, ["claude-code", "codex"])

    def test_schema_version_only_difference_merges_instead_of_aborting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            legacy = _base_event()
            legacy.pop("provenance")
            legacy["schema_version"] = "0.1"
            _write_jsonl(first, [legacy])
            _write_jsonl(
                second,
                [
                    _base_event(
                        provenance=[
                            {
                                "source": "claude-code",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ]
                    )
                ],
            )

            events = load_event_files([first, second])
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].schema_version, "0.2")
            self.assertEqual(len(events[0].provenance), 1)

    def test_genuine_conflict_still_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            _write_jsonl(first, [_base_event(data={"summary": "x"})])
            _write_jsonl(second, [_base_event(data={"summary": "different"})])

            with self.assertRaises(InputError):
                load_event_files([first, second])


if __name__ == "__main__":
    unittest.main()
