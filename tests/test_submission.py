from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from loopmetry.evaluation import ProjectEvaluator
from loopmetry.io import load_jsonl
from loopmetry.submission import (
    SubmissionError,
    build_submission,
    load_submission,
    render_submission,
    validate_submission,
    submit_envelope,
    write_private_text,
)


ROOT = Path(__file__).resolve().parents[1]


class SubmissionTests(unittest.TestCase):
    def build_demo(self) -> dict[str, object]:
        events = load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        report = ProjectEvaluator().evaluate(events)
        return build_submission(
            report,
            events,
            assignment_id="agent-ai-2026",
            submitter_id="student-001",
            source_file_count=1,
            run_id="run-test-001",
        )

    def test_submission_is_content_addressed_and_privacy_minimized(self) -> None:
        envelope = self.build_demo()
        validated = validate_submission(envelope)

        self.assertEqual(validated["submission_id"], envelope["submission_id"])
        self.assertTrue(str(envelope["submission_id"]).startswith("sha256:"))
        self.assertFalse(envelope["privacy"]["raw_transcripts_included"])
        self.assertFalse(envelope["privacy"]["canonical_events_included"])
        serialized = render_submission(envelope)
        self.assertNotIn('"events":', serialized)
        self.assertNotIn("overall_score", serialized)
        self.assertNotIn(str(ROOT), serialized)

    def test_tampering_invalidates_digest(self) -> None:
        envelope = self.build_demo()
        tampered = copy.deepcopy(envelope)
        tampered["report"]["metrics"][0]["score"] = 0.0
        with self.assertRaisesRegex(SubmissionError, "does not match"):
            validate_submission(tampered)

    def test_enrollment_identity_is_enforced(self) -> None:
        envelope = self.build_demo()
        with self.assertRaisesRegex(SubmissionError, "enrollment token"):
            validate_submission(envelope, expected_submitter_id="student-002")


    def test_plaintext_remote_upload_is_refused(self) -> None:
        envelope = self.build_demo()
        with self.assertRaisesRegex(SubmissionError, "plaintext HTTP"):
            submit_envelope("http://example.com", "secret-token", envelope)

    def test_private_round_trip(self) -> None:
        envelope = self.build_demo()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "submission.json"
            write_private_text(output, render_submission(envelope))
            loaded = load_submission(output)
            self.assertEqual(loaded["submission_id"], envelope["submission_id"])
            if hasattr(output.stat(), "st_mode"):
                self.assertEqual(output.stat().st_mode & 0o077, 0)


if __name__ == "__main__":
    unittest.main()
