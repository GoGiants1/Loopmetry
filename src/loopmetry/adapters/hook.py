"""SourceAdapter wrapper over Loopmetry's own hook-capture output files.

Discovery deliberately reuses the narrow rule from the participant workflow: only
Loopmetry-created files under ``.loopmetry/hooks/`` and ``.loopmetry/events/`` are
candidates. This adapter never reads vendor transcript formats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..event_merge import EventConflictError, merge_events
from ..hook_capture import HOOK_ADAPTER_VERSION
from ..io import load_jsonl
from ..schema import CaptureMode, Event
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
        for directory in (base / ".loopmetry" / "hooks", base / ".loopmetry" / "events"):
            if not directory.is_dir():
                continue
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
                existing = by_id.get(event.event_id)
                if existing is None:
                    by_id[event.event_id] = event
                    continue
                try:
                    by_id[event.event_id] = merge_events(existing, event)
                except EventConflictError as exc:
                    raise AdapterError(str(exc)) from exc

        events = sorted(by_id.values(), key=lambda event: (event.timestamp, event.event_id))
        coverage = CoverageReport(
            categories={
                category: (Coverage.FULL if events else Coverage.NONE)
                for category in self.capabilities().evidence_categories
            }
        )
        return AdapterRun(
            source=self.name,
            adapter_version=self.adapter_version,
            events=tuple(events),
            coverage=coverage,
        )
