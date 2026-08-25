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
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..minimize import canonical_hash, command_signature, derive_project_id, hash_text, safe_relative_path
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
        emitted_command = False
        positions: dict[str, dict[str, Any]] = (
            {key: dict(value) for key, value in checkpoint.positions.items()} if checkpoint else {}
        )
        for candidate in candidates:
            path = Path(candidate.candidate_id)
            previous_position = positions.get(candidate.candidate_id)
            previous_since = _parse_iso((previous_position or {}).get("since"))
            previous_until = _parse_iso((previous_position or {}).get("until"))
            window_changed = bool(previous_position) and not _window_is_subset(
                context.since, context.until, previous_since, previous_until
            )
            if window_changed:
                key = (
                    "window_widened",
                    "the requested time window is not contained in the window "
                    "used by the previous checkpoint advance; re-scanning the "
                    "full rollout file to recover potentially out-of-window events",
                )
                diagnostic_counts[key] = diagnostic_counts.get(key, 0) + 1
            start_index = 0 if window_changed or not previous_position else int(
                previous_position.get("records_read", 0)
            )
            previous_records_read = 0 if window_changed else (previous_position or {}).get("records_read", 0)
            pending_seed = {} if window_changed else (previous_position or {}).get("pending", {})
            session = _SessionParser(
                path=path,
                project_id=project_id,
                project_root=project_root,
                start_index=start_index,
                pending_seed=pending_seed,
            )
            events.extend(session.parse())
            events.extend(session.finalize_stalled(previous_records_read=previous_records_read))
            emitted_command = emitted_command or session.emitted_command
            for key, count in session.diagnostic_counts.items():
                diagnostic_counts[key] = diagnostic_counts.get(key, 0) + count
            position = session.position()
            position["since"] = _iso_or_none(context.since)
            position["until"] = _iso_or_none(context.until)
            positions[candidate.candidate_id] = position
        events = [event for event in events if _in_window(event, context)]
        if emitted_command:
            key = ("command_status_unavailable", "Codex's rollout format does not persist a "
                   "command exit-code/success signal; imported command status is always \"unknown\"")
            diagnostic_counts[key] = diagnostic_counts.get(key, 0) + 1
        diagnostics = tuple(
            Diagnostic(kind=kind, summary=summary, count=count)
            for (kind, summary), count in sorted(diagnostic_counts.items())
        )
        degraded = any(
            d.kind
            in {
                "unparsed_record",
                "truncated_input",
                "unresolved_tool_call",
                "stalled_tool_call",
                "unextractable_command",
                "unextractable_path",
                "command_status_unavailable",
            }
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
    if outer_since is not None and (since is None or since < outer_since):
        return False
    if outer_until is not None and (until is None or until > outer_until):
        return False
    return True


_UPDATE_FILE_RE = re.compile(r"^\*\*\* (Update|Add) File: (.+)$", re.MULTILINE)


def _patch_target_paths(patch_text: str) -> list[tuple[str, str]]:
    """Return (action, raw_path) pairs for every changed-file header in a patch.

    action is "modify" for "Update File" headers and "add" for "Add File" headers.
    """

    results: list[tuple[str, str]] = []
    for match in _UPDATE_FILE_RE.finditer(patch_text):
        verb, raw_path = match.group(1), match.group(2).strip()
        if not raw_path:
            continue
        action = "add" if verb == "Add" else "modify"
        results.append((action, raw_path))
    return results


class _SessionParser:
    """Streams one rollout file, pairing call_id across function_call/output and
    local_shell_call records (D-013's pending/finalization contract, generalized:
    Codex has two structurally different call shapes that both need it -- see
    codex_history.py's module docstring for the verified schema)."""

    def __init__(
        self,
        *,
        path: Path,
        project_id: str,
        project_root: Path,
        start_index: int,
        pending_seed: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.path = path
        self.project_id = project_id
        self.project_root = project_root
        self.start_index = start_index
        self.pending: dict[str, dict[str, Any]] = {
            key: dict(value) for key, value in pending_seed.items()
        }
        self.session_id = path.stem
        self.total_lines = 0
        self.diagnostic_counts: dict[tuple[str, str], int] = {}
        self.emitted_command = False

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
        call_id: str | None = None,
    ) -> Event:
        stable = {"session": self.session_id, "file": self.path.name, "index": index, "suffix": suffix}
        event_id = f"hist-{canonical_hash(stable)[:24]}"
        source_ref: dict[str, Any] = {"session_file": self.path.name, "record_index": index}
        if call_id is not None:
            source_ref["call_id"] = call_id
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
                        "adapter_version": CODEX_HISTORY_ADAPTER_VERSION,
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
                        break
                    line_count = index + 1
                    if index < self.start_index:
                        continue
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if len(line.encode("utf-8", errors="replace")) > _MAX_RECORD_BYTES:
                        self._count("truncated_input", "oversized rollout record skipped")
                        continue
                    try:
                        record = json.loads(stripped)
                    except json.JSONDecodeError:
                        self._count("unparsed_record", "malformed JSON line")
                        continue
                    if not isinstance(record, Mapping):
                        self._count("unparsed_record", "malformed JSON line")
                        continue
                    timestamp = record.get("timestamp")
                    if not isinstance(timestamp, str) or not timestamp.strip():
                        self._count("unparsed_record", "record missing timestamp")
                        continue
                    envelope_type = record.get("type")
                    payload = record.get("payload")
                    if envelope_type == "session_meta":
                        continue
                    if envelope_type != "response_item" or not isinstance(payload, Mapping):
                        self._count(
                            "skipped_record_type",
                            f"records of envelope type {envelope_type!r} are not imported",
                        )
                        continue
                    events.extend(self._handle_response_item(payload, index, timestamp))
        except OSError:
            pass
        self.total_lines = line_count
        return events

    def _handle_response_item(
        self, payload: Mapping[str, Any], index: int, timestamp: str
    ) -> list[Event]:
        item_type = payload.get("type")
        if item_type == "message":
            return self._handle_message(payload, index, timestamp)
        if item_type == "local_shell_call":
            return self._handle_local_shell_call(payload, index, timestamp)
        if item_type == "function_call":
            return self._handle_function_call(payload, index, timestamp)
        if item_type == "function_call_output":
            return self._handle_function_call_output(payload, timestamp)
        self._count(
            "skipped_record_type", f"response_item type {item_type!r} is not imported"
        )
        return []

    def _handle_message(
        self, payload: Mapping[str, Any], index: int, timestamp: str
    ) -> list[Event]:
        if payload.get("role") != "user":
            return []
        content = payload.get("content")
        if not isinstance(content, list):
            return []
        text = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "input_text"
        )
        if not text:
            return []
        return [
            self._event(
                index,
                timestamp,
                EventType.HUMAN_INTERVENTION,
                Actor.HUMAN,
                {
                    "action": "prompt",
                    "summary": "User submitted a prompt; content omitted.",
                    "prompt_sha256": hash_text(text),
                    "prompt_length": len(text),
                },
                suffix=f"prompt-{index}",
            )
        ]

    def _pending_entry(self, index: int, command: list[str], timestamp: str) -> dict[str, Any]:
        joined = " ".join(str(part) for part in command)
        label, verification_kind = command_signature(joined)
        is_apply_patch = bool(command) and command[0] == "apply_patch"
        patch_files: list[dict[str, str]] = []
        if is_apply_patch and len(command) > 1 and isinstance(command[1], str):
            for action, raw_path in _patch_target_paths(command[1]):
                path = safe_relative_path(raw_path, str(self.project_root))
                if path is None:
                    self._count(
                        "unextractable_path",
                        "an apply_patch call's target path could not be extracted",
                    )
                    continue
                patch_files.append({"action": action, "path": path})
        return {
            "record_index": index,
            "command_label": label,
            "command_sha256": hash_text(joined),
            "verification_kind": verification_kind,
            "timestamp": timestamp,
            "is_apply_patch": is_apply_patch,
            "patch_files": patch_files,
        }

    def _resolve(self, call_id: str, entry: Mapping[str, Any]) -> list[Event]:
        record_index = int(entry["record_index"])
        timestamp = str(entry["timestamp"])
        if entry.get("is_apply_patch"):
            patch_files = entry.get("patch_files") or []
            if not patch_files:
                self._count("unextractable_path", "an apply_patch call's target path could not be extracted")
                return []
            events: list[Event] = []
            for file_index, file_entry in enumerate(patch_files):
                action = file_entry["action"]
                path = file_entry["path"]
                events.append(
                    self._event(
                        record_index,
                        timestamp,
                        EventType.FILE_CHANGE,
                        Actor.AGENT,
                        {"path": path, "action": action},
                        suffix=f"change-{record_index}-{file_index}-{path}",
                        call_id=call_id,
                    )
                )
            return events
        self.emitted_command = True
        return [
            self._event(
                record_index,
                timestamp,
                EventType.COMMAND,
                Actor.TOOL,
                {
                    "command": entry["command_label"],
                    "status": "unknown",
                    "command_sha256": entry["command_sha256"],
                    "tool_name": "Bash",
                },
                suffix=f"command-{record_index}",
                call_id=call_id,
            )
        ]

    def _handle_local_shell_call(
        self, payload: Mapping[str, Any], index: int, timestamp: str
    ) -> list[Event]:
        call_id = payload.get("call_id")
        action = payload.get("action")
        if not isinstance(call_id, str) or not isinstance(action, Mapping):
            self._count("unextractable_command", "a local_shell_call's call_id or action was missing")
            return []
        command = action.get("command")
        if not isinstance(command, list) or not command:
            self._count("unextractable_command", "a local_shell_call's command array was missing")
            return []
        status = payload.get("status")
        if status == "in_progress":
            self.pending[call_id] = self._pending_entry(index, command, timestamp)
            return []
        entry = self.pending.pop(call_id, None) or self._pending_entry(index, command, timestamp)
        return self._resolve(call_id, entry)

    def _handle_function_call(
        self, payload: Mapping[str, Any], index: int, timestamp: str
    ) -> list[Event]:
        call_id = payload.get("call_id")
        arguments_raw = payload.get("arguments")
        if not isinstance(call_id, str) or not isinstance(arguments_raw, str):
            self._count("unextractable_command", "a function_call's call_id or arguments were missing")
            return []
        try:
            arguments = json.loads(arguments_raw)
        except json.JSONDecodeError:
            self._count("unextractable_command", "a function_call's arguments were not valid JSON")
            return []
        command = arguments.get("command") if isinstance(arguments, Mapping) else None
        if not isinstance(command, list) or not command:
            self._count("unextractable_command", "a function_call had no extractable command")
            return []
        self.pending[call_id] = self._pending_entry(index, command, timestamp)
        return []

    def _handle_function_call_output(self, payload: Mapping[str, Any], timestamp: str) -> list[Event]:
        call_id = payload.get("call_id")
        if not isinstance(call_id, str):
            return []
        entry = self.pending.pop(call_id, None)
        if entry is None:
            return []
        return self._resolve(call_id, entry)

    def finalize_stalled(self, *, previous_records_read: int) -> list[Event]:
        file_did_not_grow = self.total_lines == previous_records_read
        events: list[Event] = []
        for call_id in list(self.pending):
            entry = self.pending[call_id]
            was_already_pending = entry["record_index"] < previous_records_read
            if file_did_not_grow and was_already_pending:
                self._count("stalled_tool_call", "a call's result never arrived; session appears stalled")
                events.extend(self._resolve(call_id, entry))
                del self.pending[call_id]
            else:
                self._count("unresolved_tool_call", "a call is awaiting its result")
        return events

    def position(self) -> dict[str, Any]:
        return {
            "records_read": self.total_lines,
            "pending": {key: dict(value) for key, value in self.pending.items()},
        }
