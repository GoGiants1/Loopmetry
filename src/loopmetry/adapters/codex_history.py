"""Consented historical backfill of local Codex CLI session rollout files (D-011).

Wire format confirmed against openai/codex source (repo HEAD as of 2026-08-25):
codex-rs/protocol/src/protocol.rs (SessionMeta/SessionMetaLine/GitInfo),
codex-rs/protocol/src/models.rs (ResponseItem, ContentItem, LocalShellAction),
codex-rs/rollout/src/list.rs (path shape), codex-rs/rollout/src/policy.rs
(which items are persisted), codex-rs/core/src/tools/hook_names.rs (Bash/
apply_patch canonical tool names). Re-verify against current openai/codex main
if drift is suspected -- Codex's own docs describe this format as unstable.

Every line is {"timestamp", "type", "payload": {...}}. Only "session_meta" and
"response_item" envelope types carry evidence this adapter imports; every other
envelope type is a skip-with-count diagnostic. Discovery is bounded to sessions
whose session_meta.cwd is inside the current project root -- the same scoping
Claude Code's adapter uses, and simpler than repository-remote matching since
Codex's rollout format carries cwd directly. Transcripts are streamed read-only
and never copied; only canonical minimized events leave this module.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from ..schema import CaptureMode
from .base import (
    AdapterCapabilities,
    Diagnostic,
    DiscoveryContext,
    SourceCandidate,
)

CODEX_HISTORY_ADAPTER_VERSION = "1.0.0"
_EVENT_SOURCE = "codex"
_MAX_RECORD_BYTES = 2_000_000
_META_PROBE_LINES = 5


def _cwd_in_scope(cwd: str, project_root: Path) -> bool:
    try:
        candidate = Path(cwd).expanduser().resolve()
    except OSError:
        return False
    return candidate == project_root or project_root in candidate.parents


def _session_meta(path: Path) -> tuple[str | None, str | None]:
    """Return (cwd, session_id) from this rollout file's session_meta record."""

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _, line in zip(range(_META_PROBE_LINES), handle):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, Mapping) or record.get("type") != "session_meta":
                    continue
                payload = record.get("payload")
                if not isinstance(payload, Mapping):
                    continue
                cwd = payload.get("cwd")
                session_id = payload.get("session_id") or payload.get("id")
                if isinstance(cwd, str) and cwd:
                    return cwd, session_id if isinstance(session_id, str) else None
    except OSError:
        return None, None
    return None, None


class CodexHistoryAdapter:
    name = "codex-history"
    adapter_version = CODEX_HISTORY_ADAPTER_VERSION

    def __init__(self, codex_home: Path | None = None) -> None:
        self.codex_home = Path(codex_home) if codex_home else Path.home() / ".codex"
        self.last_discovery_diagnostics: tuple[Diagnostic, ...] = ()

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            capture_modes=(CaptureMode.HISTORY_BACKFILL,),
            evidence_categories=("file_changes", "commands", "human_turns"),
        )

    def discover(self, context: DiscoveryContext) -> tuple[SourceCandidate, ...]:
        project_root = Path(context.project_root).expanduser().resolve()
        sessions_dir = self.codex_home / "sessions"
        diagnostics: list[Diagnostic] = []
        candidates: list[SourceCandidate] = []
        if not sessions_dir.is_dir():
            self.last_discovery_diagnostics = ()
            return ()
        unattributed = 0
        for path in sorted(sessions_dir.glob("**/*.jsonl")):
            if not path.is_file():
                continue
            stat = path.stat()
            modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            cwd, session_id = _session_meta(path)
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
                    summary="rollout files whose session_meta.cwd did not match "
                    "the project root, or whose session_meta was missing/unparsable; "
                    "excluded from import",
                    count=unattributed,
                )
            )
        self.last_discovery_diagnostics = tuple(diagnostics)
        return tuple(sorted(candidates, key=lambda c: c.label))
