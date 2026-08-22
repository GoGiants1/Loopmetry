from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from loopmetry.admin_storage import AdminStorageError, AdminStore
from loopmetry.evaluation import ProjectEvaluator
from loopmetry.io import load_jsonl
from loopmetry.submission import build_submission


ROOT = Path(__file__).resolve().parents[1]


class AdminStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        self.report = ProjectEvaluator().evaluate(self.events)

    def submission(self, submitter_id: str, run_id: str) -> dict[str, object]:
        return build_submission(
            self.report,
            self.events,
            assignment_id="course-2026",
            submitter_id=submitter_id,
            source_file_count=1,
            run_id=run_id,
        )

    def test_roster_attempts_idempotency_and_manual_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdminStore(Path(directory) / "admin.db")
            first = store.enroll(
                assignment_id="course-2026",
                submitter_id="S001",
                display_name="Alice",
            )
            store.enroll(
                assignment_id="course-2026",
                submitter_id="S002",
                display_name="Bob",
            )
            participant = store.authenticate(first.token)
            self.assertIsNotNone(participant)

            inserted = store.add_submission(
                self.submission("S001", "run-first"),
                participant=participant,
            )
            self.assertFalse(inserted.duplicate)
            self.assertEqual(inserted.stored.attempt, 1)
            self.assertEqual(inserted.stored.status, "received")

            duplicate = store.add_submission(
                inserted.stored.envelope,
                participant=participant,
            )
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(duplicate.stored.attempt, 1)

            second = store.add_submission(
                self.submission("S001", "run-second"),
                participant=participant,
            )
            self.assertEqual(second.stored.attempt, 2)
            updated = store.update_status(
                second.stored.submission_id,
                "needs_revision",
                "Add integration-test evidence.",
            )
            self.assertEqual(updated.status, "needs_revision")
            self.assertIn("integration-test", updated.reviewer_note)

            overview = store.list_overview(assignment_id="course-2026")
            states = {item.participant.submitter_id: item.state for item in overview}
            self.assertEqual(states, {"S001": "needs_revision", "S002": "not_submitted"})
            history = store.list_submission_history(
                assignment_id="course-2026",
                submitter_id="S001",
            )
            self.assertEqual([item.attempt for item in history], [2, 1])
            exported = store.export_rows(assignment_id="course-2026")
            self.assertEqual(len(exported), 2)
            self.assertEqual(exported[1]["state"], "not_submitted")

    def test_token_rotation_revokes_old_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = AdminStore(Path(directory) / "admin.db")
            original = store.enroll(
                assignment_id="course-2026",
                submitter_id="S001",
            )
            with self.assertRaises(AdminStorageError):
                store.enroll(
                    assignment_id="course-2026",
                    submitter_id="S001",
                )
            rotated = store.enroll(
                assignment_id="course-2026",
                submitter_id="S001",
                rotate=True,
            )
            self.assertIsNone(store.authenticate(original.token))
            self.assertIsNotNone(store.authenticate(rotated.token))


if __name__ == "__main__":
    unittest.main()
