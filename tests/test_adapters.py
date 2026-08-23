"""Tests for the provider-neutral source-adapter contract."""

from __future__ import annotations

import json as json_module
import tempfile
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
from loopmetry.adapters.checkpoints import (
    checkpoint_path,
    load_checkpoint,
    save_checkpoint,
)
from loopmetry.adapters.hook import HookSourceAdapter


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

    def test_non_coverage_value_is_rejected(self) -> None:
        with self.assertRaises(AdapterError):
            CoverageReport(categories={"commands": "full"})

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

    def test_constructor_rejects_blank_source(self) -> None:
        with self.assertRaises(AdapterError):
            Checkpoint(source="  ", positions={})


class ModelBasicsTests(unittest.TestCase):
    def test_discovery_context_defaults(self) -> None:
        context = DiscoveryContext(project_root=Path("."))
        self.assertIsNone(context.since)
        self.assertFalse(context.interactive)

    def test_diagnostic_default_count(self) -> None:
        diagnostic = Diagnostic(kind="unparsed_record", summary="unknown record type")
        self.assertEqual(diagnostic.count, 1)


class CheckpointPersistenceTests(unittest.TestCase):
    def test_missing_checkpoint_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_checkpoint(Path(tmp), "claude-code"))

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoint = Checkpoint(
                source="claude-code",
                positions={"a": {"content_sha256": "deadbeef", "records_read": 3}},
            )
            written = save_checkpoint(root, checkpoint)
            self.assertEqual(written, checkpoint_path(root, "claude-code"))
            loaded = load_checkpoint(root, "claude-code")
            assert loaded is not None
            self.assertEqual(loaded.positions["a"]["records_read"], 3)

    def test_corrupt_checkpoint_raises_adapter_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = checkpoint_path(root, "claude-code")
            path.parent.mkdir(parents=True)
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(AdapterError):
                load_checkpoint(root, "claude-code")

    def test_source_name_is_sanitized_in_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = checkpoint_path(Path(tmp), "../evil source")
            self.assertTrue(str(path).startswith(tmp))
            self.assertNotIn("..", path.name)


def _write_hook_file(root: Path, name: str, events: list[dict]) -> Path:
    hooks_dir = root / ".loopmetry" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    path = hooks_dir / name
    path.write_text(
        "".join(json_module.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return path


def _hook_event(event_id: str) -> dict:
    return {
        "schema_version": "0.2",
        "event_id": event_id,
        "project_id": "proj",
        "session_id": "sess",
        "timestamp": "2026-08-23T10:00:00Z",
        "type": "note",
        "actor": "system",
        "source": "claude-code",
        "data": {"summary": "x"},
        "provenance": [
            {"source": "claude-code", "capture_mode": "hook", "adapter_version": "1.0.0"}
        ],
    }


class HookSourceAdapterTests(unittest.TestCase):
    def test_discover_orders_deterministically_and_stays_in_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(root, "codex.jsonl", [_hook_event("b")])
            _write_hook_file(root, "claude-code.jsonl", [_hook_event("a")])
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            candidates = adapter.discover(context)
            self.assertEqual(
                [candidate.label for candidate in candidates],
                ["claude-code.jsonl", "codex.jsonl"],
            )

    def test_import_returns_events_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(root, "claude-code.jsonl", [_hook_event("a"), _hook_event("b")])
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(len(run.events), 2)
            self.assertEqual(run.source, "hook")
            self.assertEqual(run.diagnostics, ())
            self.assertIn("commands", run.coverage.categories)

    def test_empty_project_discovers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = HookSourceAdapter()
            candidates = adapter.discover(DiscoveryContext(project_root=Path(tmp)))
            self.assertEqual(candidates, ())

    def test_capabilities_exclude_requirements(self) -> None:
        adapter = HookSourceAdapter()
        self.assertNotIn("requirements", adapter.capabilities().evidence_categories)
        self.assertIn("plans", adapter.capabilities().evidence_categories)

    def test_coverage_report_has_no_requirements_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(root, "claude-code.jsonl", [_hook_event("a")])
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertNotIn("requirements", run.coverage.categories)
            self.assertIn("commands", run.coverage.categories)

    def test_until_filters_by_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _write_hook_file(root, "claude-code.jsonl", [_hook_event("a")])
            far_past = datetime(2000, 1, 1, tzinfo=timezone.utc)
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root, until=far_past)
            candidates = adapter.discover(context)
            self.assertEqual(candidates, ())

            recent_future = datetime(2100, 1, 1, tzinfo=timezone.utc)
            context = DiscoveryContext(project_root=root, until=recent_future)
            candidates = adapter.discover(context)
            self.assertEqual([candidate.label for candidate in candidates], [path.name])


if __name__ == "__main__":
    unittest.main()
