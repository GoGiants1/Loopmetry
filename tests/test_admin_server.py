from __future__ import annotations

import base64
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from loopmetry.admin_server import create_admin_server
from loopmetry.admin_storage import AdminStore
from loopmetry.evaluation import ProjectEvaluator
from loopmetry.io import load_jsonl
from loopmetry.submission import build_submission, submit_envelope


ROOT = Path(__file__).resolve().parents[1]


class AdminServerTests(unittest.TestCase):
    def test_collection_dashboard_and_review_update(self) -> None:
        events = load_jsonl(ROOT / "examples" / "demo_project.jsonl")
        report = ProjectEvaluator().evaluate(events)
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "admin.db"
            store = AdminStore(db_path)
            enrollment = store.enroll(
                assignment_id="course-2026",
                submitter_id="S001",
                display_name="Alice",
            )
            store.enroll(
                assignment_id="course-2026",
                submitter_id="S002",
                display_name="Bob",
            )
            admin_token = "admin-token-at-least-sixteen"
            server = create_admin_server(
                database=db_path,
                admin_token=admin_token,
                bind="127.0.0.1",
                port=0,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                envelope = build_submission(
                    report,
                    events,
                    assignment_id="course-2026",
                    submitter_id="S001",
                    source_file_count=1,
                    run_id="run-server-test",
                )
                receipt = submit_envelope(base_url, enrollment.token, envelope)
                self.assertEqual(receipt.attempt, 1)
                self.assertFalse(receipt.duplicate)
                duplicate = submit_envelope(base_url, enrollment.token, envelope)
                self.assertTrue(duplicate.duplicate)

                with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                    urllib.request.urlopen(base_url + "/")
                self.assertEqual(unauthorized.exception.code, 401)

                basic = base64.b64encode(f"admin:{admin_token}".encode()).decode()
                dashboard_request = urllib.request.Request(
                    base_url + "/?assignment=course-2026",
                    headers={"Authorization": f"Basic {basic}"},
                )
                with urllib.request.urlopen(dashboard_request) as response:
                    dashboard = response.read().decode()
                self.assertIn("S001", dashboard)
                self.assertIn("S002", dashboard)
                self.assertIn("not_submitted", dashboard)

                detail_path = "/submission/" + urllib.parse.quote(
                    receipt.submission_id,
                    safe="",
                )
                detail_request = urllib.request.Request(
                    base_url + detail_path,
                    headers={"Authorization": f"Basic {basic}"},
                )
                with urllib.request.urlopen(detail_request) as response:
                    detail = response.read().decode()
                match = re.search(r'name="csrf" value="([0-9a-f]+)"', detail)
                self.assertIsNotNone(match)
                form = urllib.parse.urlencode(
                    {
                        "csrf": match.group(1),
                        "status": "accepted",
                        "note": "Reviewed with evidence.",
                    }
                ).encode()
                status_request = urllib.request.Request(
                    base_url + detail_path + "/status",
                    data=form,
                    method="POST",
                    headers={
                        "Authorization": f"Basic {basic}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
                opener = urllib.request.build_opener(
                    urllib.request.HTTPRedirectHandler()
                )
                with opener.open(status_request) as response:
                    self.assertEqual(response.status, 200)
                updated = store.get_submission(receipt.submission_id)
                self.assertEqual(updated.status, "accepted")

                export_request = urllib.request.Request(
                    base_url + "/export.csv?assignment=course-2026",
                    headers={"Authorization": f"Basic {basic}"},
                )
                with urllib.request.urlopen(export_request) as response:
                    exported = response.read().decode("utf-8-sig")
                self.assertIn("submitter_id", exported)
                self.assertIn("accepted", exported)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
