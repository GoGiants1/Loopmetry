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
from loopmetry.schema import CaptureMode, ProvenanceRecord


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

    def test_load_checkpoint_rejects_source_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_checkpoint(root, Checkpoint(source="claude-code", positions={}))
            forged_path = checkpoint_path(root, "codex")
            forged_path.parent.mkdir(parents=True, exist_ok=True)
            forged_path.write_text(
                json_module.dumps({"source": "claude-code", "positions": {}}),
                encoding="utf-8",
            )
            with self.assertRaises(AdapterError):
                load_checkpoint(root, "codex")


def _write_hook_file(root: Path, name: str, events: list[dict]) -> Path:
    hooks_dir = root / ".loopmetry" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    path = hooks_dir / name
    path.write_text(
        "".join(json_module.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    return path


def _hook_event(event_id: str, **overrides: object) -> dict:
    event = {
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
    event.update(overrides)
    return event


class HookSourceAdapterTests(unittest.TestCase):
    def test_import_candidates_merges_same_file_duplicate_event_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A retried, identical-looking hook invocation produces the same
            # deterministic event_id twice in one append-only file.
            _write_hook_file(root, "claude-code.jsonl", [_hook_event("a"), _hook_event("a")])
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(len(run.events), 1)

    def test_events_directory_file_is_not_a_hook_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(root, "claude-code.jsonl", [_hook_event("a")])
            events_dir = root / ".loopmetry" / "events"
            events_dir.mkdir(parents=True)
            (events_dir / "claude-code-history.jsonl").write_text(
                json_module.dumps(_hook_event("b")) + "\n", encoding="utf-8"
            )
            adapter = HookSourceAdapter()
            candidates = adapter.discover(DiscoveryContext(project_root=root))
            self.assertEqual([candidate.label for candidate in candidates], ["claude-code.jsonl"])

    def test_legacy_event_without_provenance_is_enriched_on_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = _hook_event("a", schema_version="0.1", provenance=[])
            _write_hook_file(root, "claude-code.jsonl", [legacy])
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(len(run.events), 1)
            event = run.events[0]
            self.assertEqual(event.schema_version, "0.2")
            self.assertEqual(len(event.provenance), 1)
            record = event.provenance[0]
            self.assertEqual(record.capture_mode.value, "hook")
            self.assertEqual(record.adapter_version, "legacy-unknown")

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

    def test_coverage_reflects_only_observed_event_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(
                root,
                "claude-code.jsonl",
                [
                    _hook_event(
                        "cmd",
                        type="command",
                        data={"command": "pytest", "status": "success"},
                    ),
                    _hook_event(
                        "read",
                        type="file_read",
                        data={"path": "src/a.py"},
                    ),
                ],
            )
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(run.coverage.categories["commands"], Coverage.FULL)
            self.assertEqual(run.coverage.categories["file_reads"], Coverage.FULL)
            self.assertEqual(run.coverage.categories["verifications"], Coverage.NONE)
            self.assertEqual(run.coverage.categories["errors"], Coverage.NONE)
            self.assertEqual(run.coverage.categories["commits"], Coverage.NONE)
            self.assertEqual(run.coverage.categories["plans"], Coverage.NONE)
            self.assertEqual(run.coverage.categories["human_turns"], Coverage.NONE)

    def test_discover_does_not_filter_by_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = _write_hook_file(root, "claude-code.jsonl", [_hook_event("a")])
            far_past = datetime(2000, 1, 1, tzinfo=timezone.utc)
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root, until=far_past)
            candidates = adapter.discover(context)
            self.assertEqual([candidate.label for candidate in candidates], [path.name])

    def test_import_candidates_filters_by_event_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(
                root,
                "claude-code.jsonl",
                [
                    _hook_event("early", timestamp="2026-08-01T00:00:00Z"),
                    _hook_event("late", timestamp="2026-08-20T00:00:00Z"),
                ],
            )
            adapter = HookSourceAdapter()
            bound = datetime(2026, 8, 10, tzinfo=timezone.utc)

            until_context = DiscoveryContext(project_root=root, until=bound)
            until_run = adapter.import_candidates(
                adapter.discover(until_context), until_context
            )
            self.assertEqual([event.event_id for event in until_run.events], ["early"])

            since_context = DiscoveryContext(project_root=root, since=bound)
            since_run = adapter.import_candidates(
                adapter.discover(since_context), since_context
            )
            self.assertEqual([event.event_id for event in since_run.events], ["late"])

    def test_late_append_does_not_exclude_earlier_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A single append-only file: mtime reflects only the last append,
            # but the window bound must still admit the earlier in-window event.
            _write_hook_file(
                root,
                "claude-code.jsonl",
                [
                    _hook_event("early", timestamp="2026-08-05T00:00:00Z"),
                    _hook_event("late", timestamp="2026-08-20T00:00:00Z"),
                ],
            )
            adapter = HookSourceAdapter()
            context = DiscoveryContext(
                project_root=root, until=datetime(2026, 8, 10, tzinfo=timezone.utc)
            )
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual([event.event_id for event in run.events], ["early"])

    def test_import_candidates_merges_duplicate_event_id_across_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(
                root,
                "claude-code.jsonl",
                [
                    _hook_event(
                        "shared",
                        provenance=[
                            {
                                "source": "claude-code",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ],
                    )
                ],
            )
            _write_hook_file(
                root,
                "codex.jsonl",
                [
                    _hook_event(
                        "shared",
                        provenance=[
                            {
                                "source": "codex",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ],
                    )
                ],
            )
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(len(run.events), 1)
            self.assertEqual(len(run.events[0].provenance), 2)

    def test_import_candidates_raises_on_genuine_conflict_across_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_hook_file(
                root, "claude-code.jsonl", [_hook_event("shared", data={"summary": "x"})]
            )
            _write_hook_file(
                root, "codex.jsonl", [_hook_event("shared", data={"summary": "different"})]
            )
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=root)
            with self.assertRaises(AdapterError):
                adapter.import_candidates(adapter.discover(context), context)

    def test_coverage_is_none_when_no_candidates_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = HookSourceAdapter()
            context = DiscoveryContext(project_root=Path(tmp))
            run = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(run.events, ())
            self.assertTrue(
                all(value == Coverage.NONE for value in run.coverage.categories.values())
            )


class CrossAdapterCapabilityTests(unittest.TestCase):
    def test_claude_code_and_codex_report_comparable_capability_shapes(self) -> None:
        from loopmetry.adapters.claude_code_history import ClaudeCodeHistoryAdapter
        from loopmetry.adapters.codex_history import CodexHistoryAdapter

        claude_caps = ClaudeCodeHistoryAdapter().capabilities()
        codex_caps = CodexHistoryAdapter().capabilities()
        self.assertTrue(set(claude_caps.evidence_categories) <= set(EVIDENCE_CATEGORIES))
        self.assertTrue(set(codex_caps.evidence_categories) <= set(EVIDENCE_CATEGORIES))
        self.assertIn(CaptureMode.HISTORY_BACKFILL, claude_caps.capture_modes)
        self.assertIn(CaptureMode.HISTORY_BACKFILL, codex_caps.capture_modes)

    def test_both_history_adapters_only_ever_report_valid_coverage_values(self) -> None:
        from loopmetry.adapters.claude_code_history import ClaudeCodeHistoryAdapter
        from loopmetry.adapters.codex_history import CodexHistoryAdapter

        for adapter in (ClaudeCodeHistoryAdapter(), CodexHistoryAdapter()):
            report = CoverageReport(
                categories={c: Coverage.FULL for c in adapter.capabilities().evidence_categories}
            )
            round_tripped = CoverageReport.from_mapping(report.to_mapping())
            self.assertEqual(round_tripped.categories, report.categories)


def _provenance_record_for(source: str, capture_mode: CaptureMode) -> ProvenanceRecord:
    return ProvenanceRecord(source=source, capture_mode=capture_mode, adapter_version="1.0.0")


class CrossSourceMergeTests(unittest.TestCase):
    """Regression guard: the generic merge machinery in event_merge and
    storage.EventStore already handles events from two different sources
    (claude-code and codex) correctly, not just within one source. Slice 4's
    hybrid ``--source auto`` orchestration builds on this; this task adds no
    new merge code.

    Note on the top-level ``Event.source`` field vs. per-record
    ``ProvenanceRecord.source``: ``event_merge.events_conflict`` compares the
    full event mapping *except* ``provenance`` and ``schema_version`` (see
    ``event_merge._comparable_mapping``), so ``Event.source`` is part of the
    identity that must match for a merge to succeed — same as project_id,
    session_id, timestamp, type, actor, and data. Two independent adapters
    that both observe the *same* canonical event (e.g. the same command run,
    picked up once via a claude-code hook and once via codex history
    backfill) therefore share one canonical top-level ``source`` while each
    contributes its own ``ProvenanceRecord`` with a distinct per-record
    ``source`` — exactly the pattern the existing same-source test
    (``test_storage.test_add_events_merges_provenance_on_duplicate_event_id``)
    already exercises for two claude-code capture modes. These tests extend
    that same pattern across the claude-code/codex boundary by varying only
    ``ProvenanceRecord.source``, holding the canonical ``Event.source``
    constant, and confirmed that varying the canonical ``Event.source``
    itself between the two sides instead makes ``events_conflict`` report a
    conflict every time (regardless of ``data``), since ``source`` is part of
    the compared mapping.
    """

    def _event(
        self,
        event_id: str,
        provenance_source: str,
        capture_mode: CaptureMode,
        *,
        canonical_source: str = "claude-code",
        data: dict | None = None,
        session_id: str = "s1",
    ):
        from loopmetry.schema import Actor, Event, EventType

        return Event(
            event_id=event_id,
            project_id="proj",
            session_id=session_id,
            timestamp=datetime(2026, 8, 25, tzinfo=timezone.utc),
            type=EventType.COMMAND,
            actor=Actor.TOOL,
            source=canonical_source,
            data=data if data is not None else {"command": "pytest", "status": "unknown"},
            provenance=(_provenance_record_for(provenance_source, capture_mode),),
        )

    def test_same_event_id_merges_across_claude_code_and_codex_sources(self) -> None:
        from loopmetry.event_merge import merge_events

        claude_side = self._event("shared-id", "claude-code", CaptureMode.HOOK)
        codex_side = self._event("shared-id", "codex", CaptureMode.HISTORY_BACKFILL)
        merged = merge_events(claude_side, codex_side)
        self.assertEqual(len(merged.provenance), 2)
        sources = {record.source for record in merged.provenance}
        self.assertEqual(sources, {"claude-code", "codex"})

    def test_genuinely_conflicting_cross_source_event_raises(self) -> None:
        from loopmetry.event_merge import EventConflictError, merge_events

        claude_side = self._event("shared-id", "claude-code", CaptureMode.HOOK)
        codex_side = self._event(
            "shared-id",
            "codex",
            CaptureMode.HISTORY_BACKFILL,
            data={"command": "ruff", "status": "unknown"},
        )
        with self.assertRaises(EventConflictError):
            merge_events(claude_side, codex_side)

    def test_event_store_ingests_mixed_source_batch_without_duplication(self) -> None:
        from loopmetry.storage import EventStore

        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            claude_side = self._event("shared-id", "claude-code", CaptureMode.HOOK)
            codex_side = self._event("shared-id", "codex", CaptureMode.HISTORY_BACKFILL)
            result = store.add_events([claude_side, codex_side])
            # A single add_events call inserts the first event (inserted=1),
            # then the second collides on event_id and is folded into the
            # first via merge_events, which adds new provenance and is
            # counted as merged=1 (see storage.EventStore.add_events and the
            # matching same-source expectations in test_storage.py).
            self.assertEqual(result.inserted, 1)
            self.assertEqual(result.merged, 1)
            self.assertEqual(result.skipped, 0)

            stored = store.list_events("proj")
            self.assertEqual(len(stored), 1)
            self.assertEqual(len(stored[0].provenance), 2)


if __name__ == "__main__":
    unittest.main()
