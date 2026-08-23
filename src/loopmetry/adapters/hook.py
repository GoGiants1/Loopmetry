"""SourceAdapter wrapper over Loopmetry's own hook-capture output files.

Discovery deliberately reuses the narrow rule from the participant workflow: only
Loopmetry-created files under ``.loopmetry/hooks/`` and ``.loopmetry/events/`` are
candidates. This adapter never reads vendor transcript formats.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..hook_capture import HOOK_ADAPTER_VERSION
from ..io import load_jsonl
from ..schema import CaptureMode
from .base import (
    EVIDENCE_CATEGORIES,
    AdapterCapabilities,
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
            evidence_categories=EVIDENCE_CATEGORIES,
        )

    def discover(self, context: DiscoveryContext) -> tuple[SourceCandidate, ...]:
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
                if context.since is not None and modified_at < context.since:
                    continue
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
        events = []
        for candidate in candidates:
            events.extend(load_jsonl(candidate.candidate_id))
        events.sort(key=lambda event: (event.timestamp, event.event_id))
        coverage = CoverageReport(
            categories={category: Coverage.FULL for category in EVIDENCE_CATEGORIES}
        )
        return AdapterRun(
            source=self.name,
            adapter_version=self.adapter_version,
            events=tuple(events),
            coverage=coverage,
        )
