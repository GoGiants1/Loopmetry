"""Consented historical backfill of local Claude Code session transcripts (D-011).

Discovery is bounded to the encoded project directory for the current project root,
and every candidate is confirmed from record content — the directory-name encoding
is lossy (both "/" and "." become "-"), so it is never trusted on its own.
Transcripts are streamed read-only and never copied; only canonical minimized
events leave this module.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from ..schema import CaptureMode
from .base import (
    AdapterCapabilities,
    Diagnostic,
    DiscoveryContext,
    ImportPreview,
    SourceCandidate,
)

CLAUDE_HISTORY_ADAPTER_VERSION = "1.0.0"
_EVENT_SOURCE = "claude-code"
_MAX_RECORD_BYTES = 2_000_000
_CWD_PROBE_LINES = 25


def encode_claude_project_dir(project_root: Path) -> str:
    resolved = str(Path(project_root).expanduser().resolve())
    return resolved.replace("/", "-").replace(".", "-")


def _cwd_in_scope(cwd: str, project_root: Path) -> bool:
    try:
        candidate = Path(cwd).expanduser().resolve()
    except OSError:
        return False
    return candidate == project_root or project_root in candidate.parents


def _session_cwd(path: Path) -> tuple[str | None, str | None]:
    """Return (cwd, session_id) from the first attributable record, streaming."""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(_CWD_PROBE_LINES), handle):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, Mapping):
                    continue
                if record.get("type") == "queue-operation":
                    continue
                cwd = record.get("cwd")
                session_id = record.get("sessionId")
                if isinstance(cwd, str) and cwd:
                    return cwd, session_id if isinstance(session_id, str) else None
    except OSError:
        return None, None
    return None, None


class ClaudeCodeHistoryAdapter:
    name = "claude-code-history"
    adapter_version = CLAUDE_HISTORY_ADAPTER_VERSION

    def __init__(self, claude_home: Path | None = None) -> None:
        self.claude_home = Path(claude_home) if claude_home else Path.home() / ".claude"
        self.last_discovery_diagnostics: tuple[Diagnostic, ...] = ()

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            capture_modes=(CaptureMode.HISTORY_BACKFILL,),
            evidence_categories=(
                "plans",
                "file_reads",
                "file_changes",
                "commands",
                "verifications",
                "errors",
                "human_turns",
            ),
        )

    def discover(self, context: DiscoveryContext) -> tuple[SourceCandidate, ...]:
        project_root = Path(context.project_root).expanduser().resolve()
        project_dir = self.claude_home / "projects" / encode_claude_project_dir(project_root)
        diagnostics: list[Diagnostic] = []
        candidates: list[SourceCandidate] = []
        if not project_dir.is_dir():
            self.last_discovery_diagnostics = ()
            return ()
        unattributed = 0
        for path in sorted(project_dir.glob("*.jsonl")):
            if not path.is_file():
                continue
            stat = path.stat()
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if context.since is not None and modified_at < context.since:
                continue
            if context.until is not None and modified_at > context.until:
                continue
            cwd, session_id = _session_cwd(path)
            if cwd is None or not _cwd_in_scope(cwd, project_root):
                unattributed += 1
                continue
            candidates.append(
                SourceCandidate(
                    candidate_id=str(path),
                    source=self.name,
                    label=path.name,
                    session_id=session_id or path.stem,
                    size_bytes=stat.st_size,
                    modified_at=modified_at,
                )
            )
        if unattributed:
            diagnostics.append(
                Diagnostic(
                    kind="unattributed_session",
                    summary="sessions in the project directory whose cwd did not match "
                    "the project root; excluded from import",
                    count=unattributed,
                )
            )
        subagent_dirs = sum(1 for entry in project_dir.iterdir() if entry.is_dir())
        if subagent_dirs:
            diagnostics.append(
                Diagnostic(
                    kind="subagent_transcripts_skipped",
                    summary="session subdirectories (subagent transcripts) are not "
                    "imported by this adapter version",
                    count=subagent_dirs,
                )
            )
        self.last_discovery_diagnostics = tuple(diagnostics)
        return tuple(candidates)

    def preview(self, candidates: Sequence[SourceCandidate]) -> ImportPreview:
        return ImportPreview(source=self.name, candidates=tuple(candidates))
