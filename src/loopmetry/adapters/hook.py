"""SourceAdapter wrapper over Loopmetry's own hook-capture output files.

Discovery is limited to ``.loopmetry/hooks/`` — the directory hook capture itself
writes to. ``.loopmetry/events/`` is reserved for other sources (explicit imports,
future historical backfill); attributing files there to this adapter would report
them with ``AdapterRun.source="hook"`` and hook coverage, which is wrong once a
non-hook adapter starts writing to that directory. The participant workflow's own
``discover_event_files()`` still scans both directories for its source-neutral
input discovery; that is unaffected by this adapter's narrower scope.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..event_merge import EventConflictError, merge_events
from ..hook_capture import HOOK_ADAPTER_VERSION
from ..io import load_jsonl
from ..schema import SCHEMA_VERSION, CaptureMode, Event, EventType, ProvenanceRecord
from .base import (
    EVIDENCE_CATEGORIES,
    AdapterCapabilities,
    AdapterError,
    AdapterRun,
    Checkpoint,
    Coverage,
    CoverageReport,
    DiscoveryContext,
    ImportPreview,
    SourceCandidate,
)


_LEGACY_ADAPTER_VERSION = "legacy-unknown"

# Which EventType corresponds to each evidence category this adapter can
# produce (per capabilities()). Categories with no mapping here (currently
# none, beyond "requirements" which capabilities() already excludes) would
# never be reportable as observed.
_CATEGORY_EVENT_TYPES: dict[str, EventType] = {
    "plans": EventType.PLAN,
    "file_reads": EventType.FILE_READ,
    "file_changes": EventType.FILE_CHANGE,
    "commands": EventType.COMMAND,
    "verifications": EventType.VERIFICATION,
    "errors": EventType.ERROR,
    "commits": EventType.COMMIT,
    "human_turns": EventType.HUMAN_INTERVENTION,
}


class HookSourceAdapter:
    """Adapts already-normalized hook capture files to the SourceAdapter contract."""

    name = "hook"
    adapter_version = HOOK_ADAPTER_VERSION

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            capture_modes=(CaptureMode.HOOK,),
            evidence_categories=tuple(
                category for category in EVIDENCE_CATEGORIES if category != "requirements"
            ),
        )

    def discover(self, context: DiscoveryContext) -> tuple[SourceCandidate, ...]:
        # Hook output is one append-only file per provider, so a file's mtime
        # reflects only its last append and cannot bound the range of event
        # timestamps inside it. Discovery therefore lists every candidate file;
        # ``since``/``until`` are enforced per-event in import_candidates().
        base = Path(context.project_root).expanduser()
        candidates: list[SourceCandidate] = []
        directory = base / ".loopmetry" / "hooks"
        if directory.is_dir():
            for path in sorted(directory.glob("*.jsonl")):
                if not path.is_file():
                    continue
                stat = path.stat()
                modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                candidates.append(
                    SourceCandidate(
                        candidate_id=str(path),
                        source=self.name,
                        label=path.name,
                        session_id=None,
                        size_bytes=stat.st_size,
                        modified_at=modified_at,
                    )
                )
        return tuple(sorted(candidates, key=lambda candidate: candidate.candidate_id))

    def preview(self, candidates: Sequence[SourceCandidate]) -> ImportPreview:
        return ImportPreview(source=self.name, candidates=tuple(candidates))

    def import_candidates(
        self,
        candidates: Sequence[SourceCandidate],
        context: DiscoveryContext,
        checkpoint: Checkpoint | None = None,
    ) -> AdapterRun:
        def in_window(event: Event) -> bool:
            if context.since is not None and event.timestamp < context.since:
                return False
            if context.until is not None and event.timestamp > context.until:
                return False
            return True

        by_id: dict[str, Event] = {}
        for candidate in candidates:
            for event in load_jsonl(candidate.candidate_id):
                if not in_window(event):
                    continue
                if not event.provenance:
                    # Pre-D-011 (schema 0.1) hook files carry no provenance. Every
                    # imported event must carry one (invariant 10), so attribute it
                    # honestly as hook-observed with an unknown original adapter
                    # version rather than leaving it empty or backdating today's
                    # adapter_version onto capture that may predate this adapter.
                    event = replace(
                        event,
                        schema_version=SCHEMA_VERSION,
                        provenance=(
                            ProvenanceRecord(
                                source=event.source,
                                capture_mode=CaptureMode.HOOK,
                                adapter_version=_LEGACY_ADAPTER_VERSION,
                            ),
                        ),
                    )
                existing = by_id.get(event.event_id)
                if existing is None:
                    by_id[event.event_id] = event
                    continue
                try:
                    by_id[event.event_id] = merge_events(existing, event)
                except EventConflictError as exc:
                    raise AdapterError(str(exc)) from exc

        events = sorted(by_id.values(), key=lambda event: (event.timestamp, event.event_id))
        observed_types = {event.type for event in events}
        coverage = CoverageReport(
            categories={
                category: (
                    Coverage.FULL if _CATEGORY_EVENT_TYPES[category] in observed_types
                    else Coverage.NONE
                )
                for category in self.capabilities().evidence_categories
            }
        )
        return AdapterRun(
            source=self.name,
            adapter_version=self.adapter_version,
            events=tuple(events),
            coverage=coverage,
        )
