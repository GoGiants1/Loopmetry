"""Tests for the provider-neutral source-adapter contract."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from loopmetry.adapters.base import (
    EVIDENCE_CATEGORIES,
    AdapterError,
    Checkpoint,
    Coverage,
    CoverageReport,
    Diagnostic,
    DiscoveryContext,
    ImportPreview,
    SourceCandidate,
)


def _candidate(candidate_id: str, size: int) -> SourceCandidate:
    return SourceCandidate(
        candidate_id=candidate_id,
        source="claude-code",
        label="session",
        session_id=candidate_id,
        size_bytes=size,
        modified_at=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )


class CoverageReportTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        report = CoverageReport(categories={"commands": Coverage.FULL, "plans": Coverage.PARTIAL})
        loaded = CoverageReport.from_mapping(report.to_mapping())
        self.assertEqual(loaded.categories["commands"], Coverage.FULL)
        self.assertEqual(loaded.categories["plans"], Coverage.PARTIAL)

    def test_unknown_category_is_rejected(self) -> None:
        with self.assertRaises(AdapterError):
            CoverageReport(categories={"vibes": Coverage.FULL})

    def test_categories_are_the_documented_set(self) -> None:
        self.assertIn("verifications", EVIDENCE_CATEGORIES)
        self.assertIn("human_turns", EVIDENCE_CATEGORIES)


class ImportPreviewTests(unittest.TestCase):
    def test_totals(self) -> None:
        preview = ImportPreview(
            source="claude-code",
            candidates=(_candidate("a", 100), _candidate("b", 250)),
        )
        self.assertEqual(preview.total_size_bytes, 350)
        self.assertEqual(preview.session_count, 2)


class CheckpointTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        checkpoint = Checkpoint(
            source="claude-code",
            positions={"a": {"content_sha256": "deadbeef", "records_read": 12}},
        )
        loaded = Checkpoint.from_mapping(checkpoint.to_mapping())
        self.assertEqual(loaded.source, "claude-code")
        self.assertEqual(loaded.positions["a"]["records_read"], 12)

    def test_rejects_non_mapping_positions(self) -> None:
        with self.assertRaises(AdapterError):
            Checkpoint.from_mapping({"source": "x", "positions": [1, 2]})


class ModelBasicsTests(unittest.TestCase):
    def test_discovery_context_defaults(self) -> None:
        context = DiscoveryContext(project_root=Path("."))
        self.assertIsNone(context.since)
        self.assertFalse(context.interactive)

    def test_diagnostic_default_count(self) -> None:
        diagnostic = Diagnostic(kind="unparsed_record", summary="unknown record type")
        self.assertEqual(diagnostic.count, 1)


if __name__ == "__main__":
    unittest.main()
