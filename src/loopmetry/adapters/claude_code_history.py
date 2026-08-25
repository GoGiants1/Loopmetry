"""Consented historical backfill of local Claude Code session transcripts (D-011).

Discovery is bounded to the encoded project directory for the current project root,
and every candidate is confirmed from record content — the directory-name encoding
is lossy (every non-alphanumeric character collapses to "-", per
https://github.com/anthropics/claude-code/issues/35162), so it is never trusted on
its own. Transcripts are streamed read-only and never copied; only canonical
minimized events leave this module.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..minimize import (
    canonical_hash,
    command_signature,
    derive_project_id,
    hash_text,
    safe_relative_path,
)
from ..schema import Actor, CaptureMode, Event, EventType
from .base import (
    AdapterCapabilities,
    AdapterRun,
    Checkpoint,
    Coverage,
    CoverageReport,
    Diagnostic,
    DiscoveryContext,
    ImportPreview,
    SourceCandidate,
)

CLAUDE_HISTORY_ADAPTER_VERSION = "1.0.0"
_EVENT_SOURCE = "claude-code"
_MAX_RECORD_BYTES = 2_000_000
_CWD_PROBE_LINES = 25

_READ_TOOLS = {"read", "readfile"}
_CHANGE_TOOLS = {"edit", "write", "multiedit", "notebookedit"}
_PLAN_TOOLS = {"exitplanmode", "todowrite", "enterplanmode"}
_VERIFICATION_STATUS_MAP = {
    "success": "passed",
    "failed": "failed",
    "unknown": "skipped",
}


_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]")


def encode_claude_project_dir(project_root: Path) -> str:
    # Claude Code collapses every non-alphanumeric character (not just "/") to
    # "-" when deriving a project's transcript directory name — confirmed both
    # from real local ~/.claude/projects/ directories on this machine (a "."
    # inside a path segment becomes "-") and from anthropics/claude-code#35162
    # (which reports "/", "-", and "_" all collapsing to "-", causing distinct
    # paths to collide). Matching only "/" and "." here would silently fail to
    # discover any project whose path contains "_", a space, ":", etc.
    resolved = str(Path(project_root).expanduser().resolve())
    return _NON_ALNUM_RE.sub("-", resolved)


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
        # A session file is appended to for as long as it is active, so its mtime
        # reflects only the last append and cannot bound the range of event
        # timestamps inside it (the same reasoning as HookSourceAdapter.discover).
        # Every candidate file is therefore listed regardless of since/until;
        # the window is enforced per-event in import_candidates() instead.
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

    def import_candidates(
        self,
        candidates: Sequence[SourceCandidate],
        context: DiscoveryContext,
        checkpoint: Checkpoint | None = None,
    ) -> AdapterRun:
        project_root = Path(context.project_root).expanduser().resolve()
        project_id = derive_project_id(str(project_root))
        events: list[Event] = []
        diagnostic_counts: dict[tuple[str, str], int] = {}
        positions: dict[str, dict[str, Any]] = (
            {key: dict(value) for key, value in checkpoint.positions.items()}
            if checkpoint
            else {}
        )
        for candidate in candidates:
            path = Path(candidate.candidate_id)
            previous_position = positions.get(candidate.candidate_id)
            start_index = _resume_index(previous_position, path, diagnostic_counts)
            # A reset (start_index == 0 with a nonempty previous position) means the
            # transcript rotated; its old pending state refers to record indexes that
            # no longer mean anything in the new file, so it must not be restored.
            rotated = bool(previous_position) and start_index == 0
            # The checkpoint's records_read advances past every line it reads,
            # regardless of since/until (the window is only applied to the final
            # events list below). That's only safe if a later import's window is
            # fully contained in the window that most recently advanced the
            # cursor — otherwise content already behind the cursor could still be
            # excluded from every prior output. It is NOT enough to compare
            # against the widest window ever requested: an unbounded import
            # followed by a narrower one (which still advances the cursor over
            # new content, just without emitting it) can permanently strand that
            # content even though "unbounded" was seen before. So track the exact
            # window the cursor last advanced under, and require the new window
            # to be a subset of it; otherwise force a full re-parse, same as a
            # transcript rotation.
            previous_since = (
                None if rotated or not previous_position else _parse_iso(previous_position.get("since"))
            )
            previous_until = (
                None if rotated or not previous_position else _parse_iso(previous_position.get("until"))
            )
            window_changed = bool(previous_position) and not rotated and not _window_is_subset(
                context.since, context.until, previous_since, previous_until
            )
            if window_changed:
                key = (
                    "window_widened",
                    "the requested time window is not contained in the window "
                    "used by the previous checkpoint advance; re-scanning the "
                    "full transcript to recover potentially out-of-window events",
                )
                diagnostic_counts[key] = diagnostic_counts.get(key, 0) + 1
                start_index = 0
            reset = rotated or window_changed
            previous_records_read = (
                0 if reset else (previous_position or {}).get("records_read", 0)
            )
            pending_seed = {} if reset else (previous_position or {}).get("pending", {})
            session = _SessionParser(
                path=path,
                project_root=project_root,
                project_id=project_id,
                start_index=start_index,
                pending_seed=pending_seed,
            )
            events.extend(session.parse())
            events.extend(
                session.finalize_stalled(previous_records_read=previous_records_read)
            )
            for key, count in session.diagnostic_counts.items():
                diagnostic_counts[key] = diagnostic_counts.get(key, 0) + count
            position = session.position()
            # Record the exact window this run's cursor advanced under (not a
            # union with any prior window) — the next import's subset check must
            # compare against this, not against history.
            position["since"] = _iso_or_none(context.since)
            position["until"] = _iso_or_none(context.until)
            positions[candidate.candidate_id] = position
        events = [event for event in events if _in_window(event, context)]
        diagnostics = tuple(
            Diagnostic(kind=kind, summary=summary, count=count)
            for (kind, summary), count in sorted(diagnostic_counts.items())
        )
        degraded = any(
            d.kind
            in {"unparsed_record", "truncated_input", "unresolved_tool_call", "stalled_tool_call"}
            for d in diagnostics
        )
        coverage = CoverageReport(
            categories={
                category: (Coverage.PARTIAL if degraded else Coverage.FULL)
                for category in self.capabilities().evidence_categories
            }
        )
        events.sort(key=lambda event: (event.timestamp, event.event_id))
        return AdapterRun(
            source=self.name,
            adapter_version=self.adapter_version,
            events=tuple(events),
            diagnostics=diagnostics,
            coverage=coverage,
            checkpoint=Checkpoint(source=self.name, positions=positions),
        )


def _in_window(event: Event, context: DiscoveryContext) -> bool:
    if context.since is not None and event.timestamp < context.since:
        return False
    if context.until is not None and event.timestamp > context.until:
        return False
    return True


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    return datetime.fromisoformat(normalized)


def _iso_or_none(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _window_is_subset(
    since: datetime | None,
    until: datetime | None,
    outer_since: datetime | None,
    outer_until: datetime | None,
) -> bool:
    """True if [since, until] could not include anything [outer_since, outer_until] excluded."""

    if outer_since is not None and (since is None or since < outer_since):
        return False
    if outer_until is not None and (until is None or until > outer_until):
        return False
    return True


def _first_line_hash(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            first_line = handle.readline()
    except OSError:
        return None
    return hash_text(first_line)


def _resume_index(
    previous_position: Mapping[str, Any] | None,
    path: Path,
    diagnostic_counts: dict[tuple[str, str], int],
) -> int:
    if not previous_position:
        return 0
    saved_hash = previous_position.get("content_sha256")
    if saved_hash is not None and saved_hash != _first_line_hash(path):
        key = (
            "checkpoint_reset",
            "transcript rotated or replaced; re-importing from the start",
        )
        diagnostic_counts[key] = diagnostic_counts.get(key, 0) + 1
        return 0
    records_read = previous_position.get("records_read", 0)
    return int(records_read) if isinstance(records_read, int) else 0


class _SessionParser:
    """Streams one transcript file, pairing Bash tool_use/tool_result blocks.

    Per D-013, an event's content is written under its event_id at most once —
    either once the real tool_result is observed, or once a full import cycle
    passes with the pending call still unresolved (a "stalled" call). Never both.
    """

    def __init__(
        self,
        *,
        path: Path,
        project_root: Path,
        project_id: str,
        start_index: int,
        pending_seed: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.path = path
        self.project_root = project_root
        self.project_id = project_id
        self.start_index = start_index
        self.pending: dict[str, dict[str, Any]] = {
            key: dict(value) for key, value in pending_seed.items()
        }
        self.session_id = path.stem
        self.last_timestamp: str | None = None
        self.total_lines = 0
        self.diagnostic_counts: dict[tuple[str, str], int] = {}

    def _count(self, kind: str, summary: str) -> None:
        key = (kind, summary)
        self.diagnostic_counts[key] = self.diagnostic_counts.get(key, 0) + 1

    def _event(
        self,
        index: int,
        timestamp: str,
        event_type: EventType,
        actor: Actor,
        data: Mapping[str, Any],
        *,
        suffix: str,
        tool_use_id: str | None = None,
    ) -> Event:
        # `suffix` must already be unique per content block within the record
        # (callers embed the block index into it) — a single assistant record
        # can contain multiple tool_use blocks (parallel tool use is a normal,
        # documented Claude Code shape), and record_index alone is shared by
        # all of them, so relying on record_index+kind+suffix without a
        # block-level discriminator would give two genuinely different tool
        # calls the same event_id.
        stable = {
            "session": self.session_id,
            "file": self.path.name,
            "index": index,
            "kind": event_type.value,
            "suffix": suffix,
        }
        event_id = f"hist-{canonical_hash(stable)[:24]}"
        source_ref: dict[str, Any] = {
            "session_file": self.path.name,
            "record_index": index,
        }
        if tool_use_id is not None:
            source_ref["tool_use_id"] = tool_use_id
        return Event.from_mapping(
            {
                "schema_version": "0.2",
                "event_id": event_id,
                "project_id": self.project_id,
                "session_id": self.session_id,
                "timestamp": timestamp,
                "type": event_type.value,
                "actor": actor.value,
                "source": _EVENT_SOURCE,
                "data": dict(data),
                "provenance": [
                    {
                        "source": _EVENT_SOURCE,
                        "capture_mode": "history-backfill",
                        "adapter_version": CLAUDE_HISTORY_ADAPTER_VERSION,
                        "source_ref": source_ref,
                    }
                ],
            }
        )

    def parse(self) -> list[Event]:
        events: list[Event] = []
        line_count = 0
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for index, line in enumerate(handle):
                    if not line.endswith("\n"):
                        # An active session keeps appending to this file; a final
                        # line with no trailing newline may still be mid-write.
                        # Stop here without counting or parsing it, so records_read
                        # never advances past it — otherwise a line that completes
                        # moments later would be permanently skipped by the next
                        # import's start_index instead of being re-read once whole.
                        break
                    line_count = index + 1
                    if index < self.start_index:
                        continue
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if len(line.encode("utf-8", errors="replace")) > _MAX_RECORD_BYTES:
                        self._count("truncated_input", "oversized transcript record skipped")
                        continue
                    try:
                        record = json.loads(stripped)
                    except json.JSONDecodeError:
                        self._count("unparsed_record", "malformed JSON line")
                        continue
                    if not isinstance(record, Mapping):
                        self._count("unparsed_record", "malformed JSON line")
                        continue
                    session_id = record.get("sessionId")
                    if isinstance(session_id, str) and session_id:
                        self.session_id = session_id
                    timestamp = record.get("timestamp")
                    if isinstance(timestamp, str) and timestamp.strip():
                        self.last_timestamp = timestamp
                    elif self.last_timestamp is None:
                        self._count("unparsed_record", "record missing timestamp")
                        continue
                    effective_timestamp = (
                        timestamp
                        if isinstance(timestamp, str) and timestamp.strip()
                        else self.last_timestamp
                    )
                    record_type = record.get("type")
                    if record_type == "user":
                        events.extend(
                            self._handle_user_record(record, index, effective_timestamp)
                        )
                    elif record_type == "assistant":
                        events.extend(
                            self._handle_assistant_record(record, index, effective_timestamp)
                        )
                    else:
                        self._count(
                            "skipped_record_type",
                            f"records of type {record_type!r} are not imported",
                        )
        except OSError:
            pass
        self.total_lines = line_count
        return events

    def _handle_user_record(
        self, record: Mapping[str, Any], index: int, timestamp: str
    ) -> list[Event]:
        message = record.get("message")
        if not isinstance(message, Mapping):
            return []
        content = message.get("content")
        if isinstance(content, str):
            if record.get("isMeta"):
                return []
            return [self._prompt_event(content, index, timestamp)]
        if isinstance(content, list):
            tool_result_blocks = [
                block
                for block in content
                if isinstance(block, Mapping) and block.get("type") == "tool_result"
            ]
            if tool_result_blocks:
                events: list[Event] = []
                for block in tool_result_blocks:
                    events.extend(self._resolve_tool_result(block))
                return events
            text_blocks = [
                block
                for block in content
                if isinstance(block, Mapping) and block.get("type") == "text"
            ]
            if text_blocks and len(text_blocks) == len(content) and not record.get("isMeta"):
                prompt = "".join(str(block.get("text", "")) for block in text_blocks)
                return [self._prompt_event(prompt, index, timestamp)]
        return []

    def _prompt_event(self, prompt: str, index: int, timestamp: str) -> Event:
        return self._event(
            index,
            timestamp,
            EventType.HUMAN_INTERVENTION,
            Actor.HUMAN,
            {
                "action": "prompt",
                "summary": "User submitted a prompt; content omitted.",
                "prompt_sha256": hash_text(prompt),
                "prompt_length": len(prompt),
            },
            suffix=f"prompt-{index}",
        )

    def _resolve_tool_result(self, block: Mapping[str, Any]) -> list[Event]:
        tool_use_id = block.get("tool_use_id")
        if not isinstance(tool_use_id, str):
            return []
        entry = self.pending.pop(tool_use_id, None)
        if entry is None:
            # Already finalized as stalled in a previous import, or never a Bash call.
            return []
        status = "failed" if block.get("is_error") else "success"
        return self._command_events(entry, status, tool_use_id=tool_use_id)

    def _command_events(
        self, entry: Mapping[str, Any], status: str, *, tool_use_id: str
    ) -> list[Event]:
        record_index = int(entry["record_index"])
        block_index = int(entry.get("block_index", 0))
        timestamp = str(entry["timestamp"])
        label = str(entry["command_label"])
        command_sha256 = str(entry["command_sha256"])
        kind = entry.get("verification_kind")
        suffix_key = f"{record_index}-{block_index}"
        events = [
            self._event(
                record_index,
                timestamp,
                EventType.COMMAND,
                Actor.TOOL,
                {
                    "command": label,
                    "status": status,
                    "command_sha256": command_sha256,
                    "tool_name": "Bash",
                },
                suffix=f"command-{suffix_key}",
                tool_use_id=tool_use_id,
            )
        ]
        if kind and status != "unknown":
            # A stalled call (status "unknown") never had a result observed, so there
            # is nothing to verify — only "known" outcomes produce a verification claim.
            events.append(
                self._event(
                    record_index,
                    timestamp,
                    EventType.VERIFICATION,
                    Actor.TOOL,
                    {
                        "kind": kind,
                        "status": _VERIFICATION_STATUS_MAP[status],
                        "command": label,
                    },
                    suffix=f"verification-{kind}-{suffix_key}",
                    tool_use_id=tool_use_id,
                )
            )
        if status == "failed":
            events.append(
                self._event(
                    record_index,
                    timestamp,
                    EventType.ERROR,
                    Actor.TOOL,
                    {
                        "code": "TOOL_EXIT_NONZERO",
                        "message": f"{label} failed; output omitted.",
                    },
                    suffix=f"command-error-{suffix_key}",
                    tool_use_id=tool_use_id,
                )
            )
        return events

    def _handle_assistant_record(
        self, record: Mapping[str, Any], index: int, timestamp: str
    ) -> list[Event]:
        message = record.get("message")
        if not isinstance(message, Mapping):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            return []
        events: list[Event] = []
        for block_index, block in enumerate(content):
            if not isinstance(block, Mapping) or block.get("type") != "tool_use":
                continue
            events.extend(self._handle_tool_use(block, index, block_index, timestamp))
        return events

    def _handle_tool_use(
        self, block: Mapping[str, Any], index: int, block_index: int, timestamp: str
    ) -> list[Event]:
        # `block_index` (the tool_use block's position within this record's content
        # list) disambiguates parallel tool calls in one assistant turn — a normal,
        # documented shape — which otherwise share the same record `index`.
        name = str(block.get("name") or "")
        lowered = name.lower()
        tool_input = block.get("input")
        tool_input = tool_input if isinstance(tool_input, Mapping) else {}
        tool_use_id = block.get("id")
        tool_use_id = tool_use_id if isinstance(tool_use_id, str) else None

        if lowered in _READ_TOOLS:
            path = safe_relative_path(tool_input.get("file_path"), str(self.project_root))
            if path is None:
                self._count("unextractable_path", "a file path could not be safely extracted")
                return []
            return [
                self._event(
                    index,
                    timestamp,
                    EventType.FILE_READ,
                    Actor.AGENT,
                    {"path": path},
                    suffix=f"read-{index}-{block_index}-{path}",
                    tool_use_id=tool_use_id,
                )
            ]

        if lowered in _CHANGE_TOOLS:
            path_value = tool_input.get("file_path") or tool_input.get("notebook_path")
            path = safe_relative_path(path_value, str(self.project_root))
            if path is None:
                self._count("unextractable_path", "a file path could not be safely extracted")
                return []
            action = "add" if lowered == "write" else "modify"
            return [
                self._event(
                    index,
                    timestamp,
                    EventType.FILE_CHANGE,
                    Actor.AGENT,
                    {"path": path, "action": action},
                    suffix=f"change-{index}-{block_index}-{path}",
                    tool_use_id=tool_use_id,
                )
            ]

        if lowered in _PLAN_TOOLS:
            return [
                self._event(
                    index,
                    timestamp,
                    EventType.PLAN,
                    Actor.AGENT,
                    {"summary": "Agent created or updated a plan; plan text omitted."},
                    suffix=f"plan-{index}-{block_index}",
                    tool_use_id=tool_use_id,
                )
            ]

        if lowered == "bash":
            command = tool_input.get("command")
            if tool_use_id is not None and isinstance(command, str) and command.strip():
                # Minimize immediately: the pending map is serialized verbatim into
                # the on-disk checkpoint JSON by position(), and a checkpoint is
                # adapter state, not a canonical event — the raw command text (which
                # can contain secrets, e.g. `curl -H "Authorization: Bearer $TOKEN"`)
                # must never be written there, matching this module's "only
                # canonical minimized events leave this module" contract.
                label, verification_kind = command_signature(command)
                self.pending[tool_use_id] = {
                    "record_index": index,
                    "block_index": block_index,
                    "command_label": label,
                    "command_sha256": hash_text(command),
                    "verification_kind": verification_kind,
                    "timestamp": timestamp,
                }
            else:
                self._count(
                    "unextractable_command",
                    "a Bash call's id or command could not be safely extracted",
                )
            return []

        return []

    def finalize_stalled(self, *, previous_records_read: int) -> list[Event]:
        # D-013 requires BOTH: the entry predates this import (it was already
        # pending when this import started, not newly stashed by this parse), AND
        # the file did not grow past where it was left — i.e. a full import cycle
        # passed with zero new lines. An unrelated record appended in between (the
        # file grew, but not with this call's result) must not finalize the call:
        # its result could still arrive in a later append, and finalizing early
        # here would drop that entry from `pending` before the real result ever
        # has a chance to resolve it.
        file_did_not_grow = self.total_lines == previous_records_read
        events: list[Event] = []
        for tool_use_id in list(self.pending):
            entry = self.pending[tool_use_id]
            was_already_pending = entry["record_index"] < previous_records_read
            if file_did_not_grow and was_already_pending:
                self._count(
                    "stalled_tool_call",
                    "a Bash call's result never arrived; session appears stalled",
                )
                events.extend(self._command_events(entry, "unknown", tool_use_id=tool_use_id))
                del self.pending[tool_use_id]
            else:
                self._count("unresolved_tool_call", "a Bash call is awaiting its result")
        return events

    def position(self) -> dict[str, Any]:
        return {
            "content_sha256": _first_line_hash(self.path),
            "records_read": self.total_lines,
            "pending": {key: dict(value) for key, value in self.pending.items()},
        }
