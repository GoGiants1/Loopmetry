# Codex Parity (Slice 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Codex the same historical-backfill and hook-integration parity Claude Code already has, behind the existing `SourceAdapter` contract, without touching slice 4 (hybrid auto mode) or any shared merge/checkpoint code.

**Architecture:** Two new, source-scoped modules mirroring the existing Claude Code ones — `adapters/codex_history.py` (a `SourceAdapter` implementation parsing `~/.codex/sessions/**/*.jsonl` rollout files) and `hook_integration_codex.py` (pure TOML-block merge/remove logic for `.codex/config.toml`) — wired into the existing `history` and `integrate` CLI subcommands via their existing per-source dispatch tables. No changes to `adapters/base.py`, `event_merge.py`, or `adapters/checkpoints.py`.

**Tech Stack:** Python 3.12+ stdlib only (`tomllib` for read-only TOML parsing/validation; no TOML writer exists or is added), `uv` for environment/test running, `unittest` for tests.

## Global Constraints

- Runtime stays standard-library-only; no new dependency is added for TOML writing (per `AGENTS.md`) — `hook_integration_codex.py` writes TOML via deterministic string templates, not a serializer library.
- Every imported event must carry `source`, `capture_mode`, `adapter_version`, and `source_ref` provenance (invariant 10 / D-011).
- Missing or unknowable evidence (in particular: Codex's rollout format never persists a command exit-code/success signal) must become an explicit gap — `status="unknown"` plus a diagnostic — never a fabricated success (invariant 4).
- History discovery is bounded to `DiscoveryContext` (`project_root`, `since`, `until`) and never reads outside it; unattributed sessions are excluded, not widened into scope (D-012).
- Hook-config writes never require `--force` when the target file doesn't exist or the result is byte-identical to what's already there; they always require `--force` (plus a single overwritten `.bak`) otherwise; an invalid existing file is a hard error on preview/apply/remove alike (D-014, extended to TOML in this plan).
- `--project-id` must never be shell-word-split or shell-interpreted: Codex's `command` field is a single shell-parsed string (confirmed — no `args` array exists in Codex's hook schema, unlike Claude Code's), so embed it via `shlex.quote()`.
- Run `uv run python -m unittest discover -s tests -v` and `uvx --from ruff==0.12.12 ruff check .` before considering any task done.

---

### Task 1: Codex historical adapter — discovery and attribution

**Files:**
- Create: `src/loopmetry/adapters/codex_history.py`
- Test: `tests/test_codex_history.py`

**Interfaces:**
- Consumes: `adapters.base.{AdapterCapabilities, AdapterRun, Checkpoint, Coverage, CoverageReport, Diagnostic, DiscoveryContext, ImportPreview, SourceCandidate}`, `schema.{Actor, CaptureMode, Event, EventType}`, `minimize.{derive_project_id}`.
- Produces: `CodexHistoryAdapter` class with `.name = "codex-history"`, `.adapter_version = CODEX_HISTORY_ADAPTER_VERSION`, `__init__(self, codex_home: Path | None = None)`, `.capabilities() -> AdapterCapabilities`, `.discover(context) -> tuple[SourceCandidate, ...]`, `.last_discovery_diagnostics: tuple[Diagnostic, ...]`. `_cwd_in_scope(cwd: str, project_root: Path) -> bool` and `_session_meta(path: Path) -> tuple[str | None, str | None]` (returns `(cwd, session_id)`) as module-level helpers Task 2/3 will also use.

This task covers discovery and attribution only; `preview()` and `import_candidates()` (which need the session parser) are Task 3.

- [ ] **Step 1: Write the failing discovery/attribution tests**

```python
# tests/test_codex_history.py
"""Tests for the Codex historical-backfill adapter (synthetic rollout files only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from loopmetry.adapters.base import DiscoveryContext
from loopmetry.adapters.codex_history import CodexHistoryAdapter


def _session_meta(cwd: str, session_id: str = "sess-0001", **extra: object) -> dict:
    payload = {
        "session_id": session_id,
        "id": session_id,
        "timestamp": "2026-08-20T09:00:00Z",
        "cwd": cwd,
        "originator": "codex_cli_rs",
        "cli_version": "0.130.0",
    }
    payload.update(extra)
    return {"timestamp": "2026-08-20T09:00:00Z", "type": "session_meta", "payload": payload}


def _write_rollout(sessions_dir: Path, name: str, records: list[dict]) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


class DiscoveryTests(unittest.TestCase):
    def _setup(self, tmp: str) -> tuple[CodexHistoryAdapter, DiscoveryContext, Path]:
        root = Path(tmp) / "work" / "project"
        root.mkdir(parents=True)
        codex_home = Path(tmp) / "codex-home"
        sessions_dir = codex_home / "sessions" / "2026" / "08" / "20"
        adapter = CodexHistoryAdapter(codex_home=codex_home)
        context = DiscoveryContext(project_root=root)
        return adapter, context, sessions_dir

    def test_no_codex_home_discovers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, _ = self._setup(tmp)
            self.assertEqual(adapter.discover(context), ())

    def test_discovers_sessions_whose_cwd_matches_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, sessions_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_rollout(sessions_dir, "rollout-b.jsonl", [_session_meta(root)])
            _write_rollout(sessions_dir, "rollout-a.jsonl", [_session_meta(root)])
            _write_rollout(
                sessions_dir, "rollout-other.jsonl", [_session_meta("/somewhere/else")]
            )
            candidates = adapter.discover(context)
            self.assertEqual(
                sorted(c.label for c in candidates), ["rollout-a.jsonl", "rollout-b.jsonl"]
            )
            self.assertEqual(candidates[0].source, "codex-history")
            self.assertEqual(candidates[0].session_id, "sess-0001")
            kinds = [d.kind for d in adapter.last_discovery_diagnostics]
            self.assertIn("unattributed_session", kinds)

    def test_discovers_sessions_nested_under_date_subdirectories(self) -> None:
        # Confirmed path shape: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
        # (codex-rs/rollout/src/list.rs). Discovery must glob recursively rather
        # than assume this exact depth, since it's an implementation detail.
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, sessions_dir = self._setup(tmp)
            _write_rollout(
                sessions_dir, "rollout-a.jsonl", [_session_meta(str(context.project_root))]
            )
            self.assertEqual(len(adapter.discover(context)), 1)

    def test_missing_session_meta_is_unattributed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, sessions_dir = self._setup(tmp)
            _write_rollout(
                sessions_dir,
                "rollout-a.jsonl",
                [{"timestamp": "2026-08-20T09:00:00Z", "type": "turn_context", "payload": {}}],
            )
            self.assertEqual(adapter.discover(context), ())
            kinds = [d.kind for d in adapter.last_discovery_diagnostics]
            self.assertIn("unattributed_session", kinds)

    def test_discover_does_not_filter_by_file_mtime(self) -> None:
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, sessions_dir = self._setup(tmp)
            _write_rollout(
                sessions_dir, "rollout-a.jsonl", [_session_meta(str(context.project_root))]
            )
            future = DiscoveryContext(
                project_root=context.project_root,
                since=datetime(2999, 1, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(len(adapter.discover(future)), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_codex_history -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loopmetry.adapters.codex_history'`

- [ ] **Step 3: Implement discovery and attribution**

```python
# src/loopmetry/adapters/codex_history.py
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
from typing import Mapping, Sequence

from ..minimize import derive_project_id
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
```

Note: `derive_project_id` is imported here because Task 3's `import_candidates` needs it; keeping the import in this file now avoids a diff churn later. `Sequence` is imported for the `preview`/`import_candidates` signatures Task 3 adds to this same class — leave the unused-import lint to Task 3, or drop `Sequence` from this step's import list if `ruff` flags it before Task 3 lands (check with `uvx --from ruff==0.12.12 ruff check src/loopmetry/adapters/codex_history.py` after this step; remove `Sequence` from the import line if it's unused yet).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_codex_history -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check src/loopmetry/adapters/codex_history.py tests/test_codex_history.py
git add src/loopmetry/adapters/codex_history.py tests/test_codex_history.py
git commit -m "feat: add Codex historical adapter discovery and cwd-based attribution"
```

---

### Task 2: Codex historical adapter — session parser (message, local_shell_call, function_call)

**Files:**
- Modify: `src/loopmetry/adapters/codex_history.py`
- Modify: `tests/test_codex_history.py`

**Interfaces:**
- Consumes: Task 1's `_EVENT_SOURCE`, `CODEX_HISTORY_ADAPTER_VERSION`, `_MAX_RECORD_BYTES`; `minimize.{canonical_hash, command_signature, hash_text}`; `schema.{Actor, Event, EventType}`.
- Produces: `_SessionParser` class with `__init__(self, *, path, project_id, start_index, pending_seed)`, `.parse() -> list[Event]`, `.finalize_stalled(*, previous_records_read: int) -> list[Event]`, `.position() -> dict`, `.diagnostic_counts: dict[tuple[str, str], int]`, `.total_lines: int`. Task 3's `import_candidates` calls these exactly as `claude_code_history.py`'s `import_candidates` calls `_SessionParser` today.

- [ ] **Step 1: Write the failing parser tests**

```python
# append to tests/test_codex_history.py, before `if __name__ == "__main__":`

from loopmetry.adapters.base import Checkpoint, Coverage, DiscoveryContext as _DC


def _response_item(payload: dict, timestamp: str = "2026-08-20T09:01:00Z") -> dict:
    return {"timestamp": timestamp, "type": "response_item", "payload": payload}


def _user_message(text: str, timestamp: str = "2026-08-20T09:01:00Z") -> dict:
    return _response_item(
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        timestamp,
    )


def _local_shell_call(
    call_id: str, command: list[str], status: str = "completed", timestamp: str = "2026-08-20T09:01:05Z"
) -> dict:
    return _response_item(
        {
            "type": "local_shell_call",
            "call_id": call_id,
            "status": status,
            "action": {"type": "exec", "command": command},
        },
        timestamp,
    )


def _function_call(call_id: str, name: str, arguments: dict, timestamp: str = "2026-08-20T09:01:05Z") -> dict:
    return _response_item(
        {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": json.dumps(arguments),
        },
        timestamp,
    )


def _function_call_output(call_id: str, output: str = "ok", timestamp: str = "2026-08-20T09:01:06Z") -> dict:
    return _response_item({"type": "function_call_output", "call_id": call_id, "output": output}, timestamp)


class ImportTests(unittest.TestCase):
    def _import(self, records: list[dict]):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            codex_home = Path(tmp) / "codex-home"
            sessions_dir = codex_home / "sessions" / "2026" / "08" / "20"
            full = [_session_meta(str(root))] + records
            _write_rollout(sessions_dir, "rollout-a.jsonl", full)
            adapter = CodexHistoryAdapter(codex_home=codex_home)
            context = _DC(project_root=root)
            candidates = adapter.discover(context)
            return adapter.import_candidates(candidates, context), context

    def test_user_message_becomes_human_intervention_event(self) -> None:
        run, _ = self._import([_user_message("do the thing")])
        self.assertEqual(len(run.events), 1)
        event = run.events[0]
        self.assertEqual(event.type.value, "human_intervention")
        self.assertEqual(event.source, "codex")
        self.assertNotIn("do the thing", json.dumps(event.data))

    def test_local_shell_call_completed_in_one_record_becomes_command_with_unknown_status(self) -> None:
        run, _ = self._import([_local_shell_call("call-1", ["bash", "-lc", "pytest"])])
        commands = [e for e in run.events if e.type.value == "command"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].data["status"], "unknown")
        self.assertEqual(commands[0].data["command"], "pytest")
        kinds = [d.kind for d in run.diagnostics]
        self.assertIn("command_status_unavailable", kinds)

    def test_apply_patch_call_becomes_file_change_event(self) -> None:
        patch = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-old\n+new\n*** End Patch"
        run, _ = self._import([_local_shell_call("call-2", ["apply_patch", patch])])
        changes = [e for e in run.events if e.type.value == "file_change"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].data["path"], "src/app.py")

    def test_function_call_and_output_pair_across_two_records(self) -> None:
        run, _ = self._import(
            [
                _function_call("call-3", "shell", {"command": ["bash", "-lc", "ruff check ."]}),
                _function_call_output("call-3", "0 errors"),
            ]
        )
        commands = [e for e in run.events if e.type.value == "command"]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].data["status"], "unknown")

    def test_unresolved_call_is_pending_not_dropped(self) -> None:
        run, _ = self._import([_function_call("call-4", "shell", {"command": ["bash", "-lc", "sleep 1"]})])
        self.assertEqual([e for e in run.events if e.type.value == "command"], [])
        kinds = [d.kind for d in run.diagnostics]
        self.assertIn("unresolved_tool_call", kinds)
        self.assertIsNotNone(run.checkpoint)
        positions = run.checkpoint.positions
        pending = next(iter(positions.values()))["pending"]
        self.assertIn("call-4", pending)

    def test_unknown_response_item_type_is_diagnosed_not_dropped_silently(self) -> None:
        run, _ = self._import([_response_item({"type": "reasoning", "summary": []})])
        self.assertEqual(run.events, ())
        kinds = [d.kind for d in run.diagnostics]
        self.assertIn("skipped_record_type", kinds)

    def test_coverage_is_partial_when_any_command_is_emitted(self) -> None:
        run, _ = self._import([_local_shell_call("call-5", ["bash", "-lc", "pytest"])])
        self.assertEqual(run.coverage.categories["commands"], Coverage.PARTIAL)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_codex_history -v`
Expected: FAIL — `AttributeError: 'CodexHistoryAdapter' object has no attribute 'import_candidates'`

- [ ] **Step 3: Implement the session parser**

Append to `src/loopmetry/adapters/codex_history.py` (imports section grows too — replace the top import block):

```python
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..minimize import canonical_hash, command_signature, derive_project_id, hash_text
from ..schema import Actor, CaptureMode, Event, EventType
from .base import (
    AdapterCapabilities,
    Diagnostic,
    DiscoveryContext,
    SourceCandidate,
)
```

```python
_UPDATE_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add) File: (.+)$", re.MULTILINE)


def _patch_target_path(patch_text: str) -> str | None:
    match = _UPDATE_FILE_RE.search(patch_text)
    if not match:
        return None
    return match.group(1).strip() or None


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
        start_index: int,
        pending_seed: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.path = path
        self.project_id = project_id
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
        return {
            "record_index": index,
            "command_label": label,
            "command_sha256": hash_text(joined),
            "verification_kind": verification_kind,
            "timestamp": timestamp,
            "is_apply_patch": bool(command) and command[0] == "apply_patch",
            "patch_target": _patch_target_path(command[1]) if len(command) > 1 and command[0] == "apply_patch" else None,
        }

    def _resolve(self, call_id: str, entry: Mapping[str, Any]) -> list[Event]:
        record_index = int(entry["record_index"])
        timestamp = str(entry["timestamp"])
        if entry.get("is_apply_patch"):
            path = entry.get("patch_target")
            if path is None:
                self._count("unextractable_path", "an apply_patch call's target path could not be extracted")
                return []
            return [
                self._event(
                    record_index,
                    timestamp,
                    EventType.FILE_CHANGE,
                    Actor.AGENT,
                    {"path": path, "action": "modify"},
                    suffix=f"change-{record_index}-{path}",
                    call_id=call_id,
                )
            ]
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_codex_history -v`
Expected: still FAIL at this step (`import_candidates` doesn't exist yet — that's Task 3). Confirm the failure is specifically `AttributeError: 'CodexHistoryAdapter' object has no attribute 'import_candidates'` and nothing else (i.e. the parser code itself imports and compiles cleanly): run `uv run python -c "from loopmetry.adapters.codex_history import _SessionParser"` and confirm no `ImportError`/`SyntaxError`.

- [ ] **Step 5: Commit**

```bash
git add src/loopmetry/adapters/codex_history.py tests/test_codex_history.py
git commit -m "feat: add Codex rollout session parser (message/local_shell_call/function_call)"
```

---

### Task 3: Codex historical adapter — import_candidates, checkpoint resume, coverage

**Files:**
- Modify: `src/loopmetry/adapters/codex_history.py`
- Modify: `tests/test_codex_history.py`

**Interfaces:**
- Consumes: Task 1's `CodexHistoryAdapter.discover`; Task 2's `_SessionParser`.
- Produces: `CodexHistoryAdapter.preview(candidates) -> ImportPreview` and `CodexHistoryAdapter.import_candidates(candidates, context, checkpoint=None) -> AdapterRun`, matching `claude_code_history.py`'s signatures exactly (later tasks and `cli.py`'s `_run_history` treat every `_HISTORY_ADAPTERS` entry uniformly).

This task is a near-direct adaptation of `claude_code_history.py`'s `import_candidates`/window-tracking helpers (`_in_window`, `_parse_iso`, `_iso_or_none`, `_window_is_subset`, `_resume_index`) — Codex's checkpoint has no `content_sha256` rotation check because rollout filenames already embed a creation timestamp+uuid (a rotated/replaced file would be a different candidate_id, not a reused one), so `_resume_index` here is simpler: just `records_read` from the previous position, with no hash comparison.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_codex_history.py, inside ImportTests (or a new class)

class CheckpointResumeTests(unittest.TestCase):
    def test_second_import_only_processes_new_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            codex_home = Path(tmp) / "codex-home"
            sessions_dir = codex_home / "sessions" / "2026" / "08" / "20"
            path = _write_rollout(
                sessions_dir, "rollout-a.jsonl", [_session_meta(str(root)), _user_message("first")]
            )
            adapter = CodexHistoryAdapter(codex_home=codex_home)
            context = _DC(project_root=root)
            candidates = adapter.discover(context)
            first_run = adapter.import_candidates(candidates, context)
            self.assertEqual(len(first_run.events), 1)

            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_user_message("second")) + "\n")
            candidates_again = adapter.discover(context)
            second_run = adapter.import_candidates(
                candidates_again, context, checkpoint=first_run.checkpoint
            )
            self.assertEqual(len(second_run.events), 1)
            self.assertNotEqual(first_run.events[0].event_id, second_run.events[0].event_id)

    def test_import_candidates_filters_by_event_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            codex_home = Path(tmp) / "codex-home"
            sessions_dir = codex_home / "sessions" / "2026" / "08" / "20"
            _write_rollout(
                sessions_dir,
                "rollout-a.jsonl",
                [
                    _session_meta(str(root)),
                    _user_message("old", timestamp="2020-01-01T00:00:00Z"),
                    _user_message("new", timestamp="2026-08-20T09:01:00Z"),
                ],
            )
            adapter = CodexHistoryAdapter(codex_home=codex_home)
            from datetime import datetime, timezone

            context = _DC(project_root=root, since=datetime(2025, 1, 1, tzinfo=timezone.utc))
            candidates = adapter.discover(context)
            run = adapter.import_candidates(candidates, context)
            self.assertEqual(len(run.events), 1)

    def test_preview_reports_session_and_size_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            codex_home = Path(tmp) / "codex-home"
            sessions_dir = codex_home / "sessions" / "2026" / "08" / "20"
            _write_rollout(sessions_dir, "rollout-a.jsonl", [_session_meta(str(root))])
            adapter = CodexHistoryAdapter(codex_home=codex_home)
            context = _DC(project_root=root)
            candidates = adapter.discover(context)
            preview = adapter.preview(candidates)
            self.assertEqual(preview.session_count, 1)
            self.assertGreater(preview.total_size_bytes, 0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_codex_history -v`
Expected: FAIL — `AttributeError: 'CodexHistoryAdapter' object has no attribute 'import_candidates'`

- [ ] **Step 3: Implement `preview` and `import_candidates`**

Add to the `CodexHistoryAdapter` class in `src/loopmetry/adapters/codex_history.py` (and the module-level window helpers below it):

```python
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
```

Add `AdapterRun, Checkpoint, Coverage, CoverageReport, ImportPreview` to the `from .base import (...)` line at the top of the module.

- [ ] **Step 4: Run the full adapter test file**

Run: `uv run python -m unittest tests.test_codex_history -v`
Expected: PASS (all tests from Tasks 1-3)

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check src/loopmetry/adapters/codex_history.py tests/test_codex_history.py
git add src/loopmetry/adapters/codex_history.py tests/test_codex_history.py
git commit -m "feat: complete Codex historical adapter (import, checkpoint resume, coverage)"
```

---

### Task 4: Wire `loopmetry history --source codex` into the CLI

**Files:**
- Modify: `src/loopmetry/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 3's `CodexHistoryAdapter` (`src/loopmetry/adapters/codex_history.py`).
- Produces: `history discover|preview|import --source codex` behaves identically in shape to `--source claude-code`, with output defaulting to `.loopmetry/events/codex-history.jsonl`.

- [ ] **Step 1: Write the failing CLI test**

Find `tests/test_cli.py`'s existing `--source claude-code` history-import test (search for `"history"` and `"claude-code"` to find its exact name and fixture style before writing this — mirror its structure). Add a parallel test:

```python
def test_history_import_source_codex_writes_default_output_path(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        root.mkdir()
        codex_home = Path(tmp) / "codex-home"
        sessions_dir = codex_home / "sessions" / "2026" / "08" / "20"
        sessions_dir.mkdir(parents=True)
        record = {
            "timestamp": "2026-08-20T09:00:00Z",
            "type": "session_meta",
            "payload": {"session_id": "s1", "cwd": str(root)},
        }
        (sessions_dir / "rollout-a.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        env = dict(os.environ, LOOPMETRY_CODEX_HOME=str(codex_home))
        with unittest.mock.patch.dict(os.environ, env):
            exit_code = main(
                ["history", "import", "--source", "codex", "--root", str(root), "--yes"]
            )
        self.assertEqual(exit_code, 0)
        self.assertTrue((root / ".loopmetry" / "events" / "codex-history.jsonl").exists())
```

(Adjust imports at the top of the test file — `os`, `unittest.mock`, `main` — to match whatever's already imported; add only what's missing.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m unittest tests.test_cli -v -k codex`
Expected: FAIL — `argparse` error, `--source codex` not in the allowed `choices` for `history`.

- [ ] **Step 3: Wire the adapter into `cli.py`**

In `src/loopmetry/cli.py`:

1. Add the import (near the existing `from .adapters.claude_code_history import ClaudeCodeHistoryAdapter`):

```python
from .adapters.codex_history import CodexHistoryAdapter
```

2. Extend the dispatch table (near line 54):

```python
DEFAULT_CODEX_HOME_ENV = "LOOPMETRY_CODEX_HOME"
_HISTORY_ADAPTERS: dict[str, type] = {
    "claude-code": ClaudeCodeHistoryAdapter,
    "codex": CodexHistoryAdapter,
}
```

3. In `_run_history`, generalize the two source-specific pieces (the home-dir env var and the default output filename). Replace:

```python
    claude_home_raw = os.environ.get(DEFAULT_CLAUDE_HOME_ENV)
    claude_home = Path(claude_home_raw).expanduser() if claude_home_raw else None
    adapter = _HISTORY_ADAPTERS[args.source](claude_home=claude_home)
```

with:

```python
    _HOME_ENV_BY_SOURCE = {"claude-code": DEFAULT_CLAUDE_HOME_ENV, "codex": DEFAULT_CODEX_HOME_ENV}
    _HOME_KWARG_BY_SOURCE = {"claude-code": "claude_home", "codex": "codex_home"}
    home_raw = os.environ.get(_HOME_ENV_BY_SOURCE[args.source])
    home = Path(home_raw).expanduser() if home_raw else None
    adapter = _HISTORY_ADAPTERS[args.source](**{_HOME_KWARG_BY_SOURCE[args.source]: home})
```

and replace the default-output-path line inside the `import` branch:

```python
        output_path = (
            Path(args.output).expanduser()
            if args.output
            else root / ".loopmetry" / "events" / "claude-code-history.jsonl"
        )
```

with:

```python
        _DEFAULT_OUTPUT_NAME = {"claude-code": "claude-code-history.jsonl", "codex": "codex-history.jsonl"}
        output_path = (
            Path(args.output).expanduser()
            if args.output
            else root / ".loopmetry" / "events" / _DEFAULT_OUTPUT_NAME[args.source]
        )
```

(Move the two small dicts, `_HOME_ENV_BY_SOURCE`/`_HOME_KWARG_BY_SOURCE`/`_DEFAULT_OUTPUT_NAME`, to module scope next to `_HISTORY_ADAPTERS` rather than inline, once the tests pass — inline above only for clarity of exactly what changes; keep them next to `_HISTORY_ADAPTERS` in the final diff.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_cli -v -k codex && uv run python -m unittest discover -s tests -v`
Expected: PASS, and the full suite (including existing `--source claude-code` tests) still passes.

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check src/loopmetry/cli.py tests/test_cli.py
git add src/loopmetry/cli.py tests/test_cli.py
git commit -m "feat: wire loopmetry history --source codex into the CLI"
```

---

### Task 5: Codex hook-config installer — pure TOML block logic

**Files:**
- Create: `src/loopmetry/hook_integration_codex.py`
- Test: `tests/test_hook_integration_codex.py`

**Interfaces:**
- Consumes: stdlib `tomllib`, `re`, `shlex`.
- Produces: `merge_config(existing_text: str, project_id: str | None) -> tuple[str, bool]`, `remove_config(existing_text: str) -> tuple[str, bool]`, `build_hook_command(project_id: str | None) -> str`, `CODEX_INTEGRATION_HOOK_EVENTS` (reuse `hook_integration.INTEGRATION_HOOK_EVENTS`'s five names). Unlike the JSON installer's `merge_settings`/`remove_settings` (which take/return a parsed `dict`), these take/return raw **text**, because TOML has no stdlib writer — `cli.py`'s `_run_integrate` (Task 6) still owns existing-file reading, diffing, backup, and the force policy, but for Codex it hands this module text, not a parsed structure.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hook_integration_codex.py
import unittest

from loopmetry.hook_integration_codex import build_hook_command, merge_config, remove_config


class BuildHookCommandTests(unittest.TestCase):
    def test_no_project_id(self) -> None:
        self.assertEqual(build_hook_command(None), "loopmetry capture-hook --source codex")

    def test_project_id_with_spaces_is_shell_quoted(self) -> None:
        command = build_hook_command("course 2026; rm -rf /")
        self.assertIn("'course 2026; rm -rf /'", command)
        import shlex

        tokens = shlex.split(command)
        self.assertEqual(tokens[-2:], ["--project-id", "course 2026; rm -rf /"])


class MergeConfigTests(unittest.TestCase):
    def test_merge_into_empty_file_adds_all_events(self) -> None:
        merged, changed = merge_config("", None)
        self.assertTrue(changed)
        for event in ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "TaskCompleted", "SessionEnd"):
            self.assertIn(f"[[hooks.{event}]]", merged)
            self.assertIn(f"[[hooks.{event}.hooks]]", merged)
        self.assertIn('command = "loopmetry capture-hook --source codex"', merged)
        self.assertIn("timeout = 3", merged)

    def test_merge_is_idempotent(self) -> None:
        once, _ = merge_config("", None)
        twice, changed = merge_config(once, None)
        self.assertFalse(changed)
        self.assertEqual(once, twice)

    def test_merge_preserves_unrelated_toml_content(self) -> None:
        existing = '[model]\nname = "gpt-5"\n\n[[hooks.UserPromptSubmit]]\n\n[[hooks.UserPromptSubmit.hooks]]\ntype = "command"\ncommand = "other-tool"\ntimeout = 5\n'
        merged, changed = merge_config(existing, None)
        self.assertTrue(changed)
        self.assertIn('name = "gpt-5"', merged)
        self.assertIn('command = "other-tool"', merged)
        self.assertIn('command = "loopmetry capture-hook --source codex"', merged)

    def test_changing_project_id_replaces_rather_than_duplicates(self) -> None:
        once, _ = merge_config("", None)
        merged, changed = merge_config(once, "my-project")
        self.assertTrue(changed)
        self.assertEqual(merged.count("[[hooks.UserPromptSubmit]]"), 1)
        self.assertIn("--project-id my-project", merged)

    def test_invalid_existing_toml_raises(self) -> None:
        with self.assertRaises(ValueError):
            merge_config("not [ valid toml", None)

    def test_hooks_value_not_a_table_raises(self) -> None:
        with self.assertRaises(ValueError):
            merge_config("hooks = 1\n", None)


class RemoveConfigTests(unittest.TestCase):
    def test_remove_on_file_with_nothing_managed_is_noop(self) -> None:
        existing = '[model]\nname = "gpt-5"\n'
        merged, changed = remove_config(existing)
        self.assertFalse(changed)
        self.assertEqual(merged, existing)

    def test_remove_strips_only_managed_blocks(self) -> None:
        merged_after_apply, _ = merge_config(
            '[[hooks.UserPromptSubmit]]\n\n[[hooks.UserPromptSubmit.hooks]]\ntype = "command"\ncommand = "other-tool"\ntimeout = 5\n',
            None,
        )
        merged, changed = remove_config(merged_after_apply)
        self.assertTrue(changed)
        self.assertIn('command = "other-tool"', merged)
        self.assertNotIn("loopmetry capture-hook", merged)

    def test_remove_never_touches_events_outside_installer_scope(self) -> None:
        existing = '[[hooks.SessionStart]]\n\n[[hooks.SessionStart.hooks]]\ntype = "command"\ncommand = "loopmetry capture-hook --source codex --output custom.jsonl"\ntimeout = 3\n'
        merged, changed = remove_config(existing)
        self.assertFalse(changed)
        self.assertEqual(merged, existing)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_hook_integration_codex -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'loopmetry.hook_integration_codex'`

- [ ] **Step 3: Implement the module**

```python
# src/loopmetry/hook_integration_codex.py
"""Deterministic generation and text-level merging of local Codex hook config.

Pure logic only -- no filesystem access (cli.py owns reading, diffing, backup,
and the force policy, same split as hook_integration.py). Codex's hook config
is TOML with no stdlib writer, so unlike hook_integration.py (which returns a
parsed dict cli.py re-serializes), this module returns raw text: tomllib
parses/validates the whole file and each individual candidate block in
isolation (every "[[hooks.<Event>]]" occurrence is independently valid TOML on
its own, so this needs no full round-trip serializer), but writing replaces
only the located block spans, leaving every other byte -- comments, formatting,
unrelated tables -- untouched.

Codex's command field has no args array (unlike Claude Code's JSON installer):
it is a single shell-parsed string, so a project_id containing spaces or shell
metacharacters is embedded via shlex.quote() rather than passed as a separate
exec-form argument.
"""

from __future__ import annotations

import re
import shlex
import tomllib

INTEGRATION_HOOK_EVENTS = (
    "UserPromptSubmit",
    "PostToolUse",
    "PostToolUseFailure",
    "TaskCompleted",
    "SessionEnd",
)

_BASE_ARGS = ["loopmetry", "capture-hook", "--source", "codex"]
_TIMEOUT = 3


def build_hook_command(project_id: str | None) -> str:
    if project_id:
        return " ".join([*_BASE_ARGS, "--project-id", shlex.quote(project_id)])
    return " ".join(_BASE_ARGS)


def _owned_command_args(command: object) -> list[str] | None:
    if not isinstance(command, str):
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    prefix, remainder = tokens[: len(_BASE_ARGS)], tokens[len(_BASE_ARGS) :]
    if prefix != _BASE_ARGS:
        return None
    if remainder and (len(remainder) != 2 or remainder[0] != "--project-id"):
        return None
    return tokens


def _validate_whole_file(existing_text: str) -> dict:
    try:
        parsed = tomllib.loads(existing_text) if existing_text.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"existing file is not valid TOML: {exc}") from exc
    hooks = parsed.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("'hooks' must be a TOML table")
    for event in INTEGRATION_HOOK_EVENTS:
        if event in hooks and not isinstance(hooks[event], list):
            raise ValueError(f"'hooks.{event}' must be a TOML array of tables")
    return parsed


def _block_spans(existing_text: str, event: str) -> list[tuple[int, int]]:
    """Byte spans of every bare `[[hooks.<event>]]` occurrence, each running to
    just before the next *non-continuation* header line or EOF.

    A naive "next line starting with `[`" boundary would match this block's own
    nested `[[hooks.<event>.hooks]]` sub-header and truncate the span to just
    its first two lines, before `_span_is_owned` ever sees the handler table.
    The boundary regex below excludes exactly that one continuation shape
    (`[[hooks.<event>.` ...) while still treating a *repeated* bare
    `[[hooks.<event>]]` (a second array entry) or any other table's header as a
    real boundary.
    """

    escaped_event = re.escape(event)
    header_re = re.compile(rf"(?m)^\[\[hooks\.{escaped_event}\]\][ \t]*$")
    boundary_re = re.compile(rf"(?m)^\[(?!\[hooks\.{escaped_event}\.)")
    spans: list[tuple[int, int]] = []
    for match in header_re.finditer(existing_text):
        start = match.start()
        next_header = boundary_re.search(existing_text, match.end())
        end = next_header.start() if next_header else len(existing_text)
        spans.append((start, end))
    return spans


def _span_is_owned(existing_text: str, span: tuple[int, int]) -> list[str] | None:
    start, end = span
    try:
        parsed = tomllib.loads(existing_text[start:end])
    except tomllib.TOMLDecodeError:
        return None
    hooks = parsed.get("hooks")
    if not isinstance(hooks, dict) or len(hooks) != 1:
        return None
    (entries,) = hooks.values()
    if not isinstance(entries, list) or len(entries) != 1:
        return None
    entry = entries[0]
    if not isinstance(entry, dict) or set(entry.keys()) != {"hooks"}:
        return None
    handlers = entry["hooks"]
    if not isinstance(handlers, list) or len(handlers) != 1:
        return None
    handler = handlers[0]
    if not isinstance(handler, dict) or set(handler.keys()) != {"type", "command", "timeout"}:
        return None
    if handler.get("type") != "command" or handler.get("timeout") != _TIMEOUT:
        return None
    return _owned_command_args(handler.get("command"))


def _render_block(event: str, project_id: str | None) -> str:
    command = build_hook_command(project_id)
    escaped = command.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f"[[hooks.{event}]]\n\n"
        f"[[hooks.{event}.hooks]]\n"
        f'type = "command"\n'
        f'command = "{escaped}"\n'
        f"timeout = {_TIMEOUT}\n"
    )


def merge_config(existing_text: str, project_id: str | None) -> tuple[str, bool]:
    _validate_whole_file(existing_text)
    desired_args = shlex.split(build_hook_command(project_id))
    text = existing_text
    changed = False
    for event in INTEGRATION_HOOK_EVENTS:
        spans = _block_spans(text, event)
        owned = [(span, _span_is_owned(text, span)) for span in spans]
        owned = [(span, args) for span, args in owned if args is not None]
        if len(owned) == 1 and owned[0][1] == desired_args:
            continue
        changed = True
        for span, _ in sorted(owned, key=lambda item: item[0][0], reverse=True):
            start, end = span
            text = text[:start] + text[end:]
        separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        text = text + separator + _render_block(event, project_id)
    return text, changed


def remove_config(existing_text: str) -> tuple[str, bool]:
    _validate_whole_file(existing_text)
    text = existing_text
    changed = False
    for event in INTEGRATION_HOOK_EVENTS:
        spans = _block_spans(text, event)
        owned_spans = [span for span in spans if _span_is_owned(text, span) is not None]
        if not owned_spans:
            continue
        changed = True
        for start, end in sorted(owned_spans, reverse=True):
            text = text[:start] + text[end:]
    return text, changed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_hook_integration_codex -v`
Expected: PASS. If `test_merge_preserves_unrelated_toml_content` fails because the foreign `[[hooks.UserPromptSubmit]]` block for `other-tool` got removed too, check `_span_is_owned`: it must return `None` for that block (its `command` is `"other-tool"`, which `_owned_command_args` correctly rejects since its first token isn't `loopmetry`) — if it's failing, the bug is almost certainly in `_block_spans`' span boundaries overlapping incorrectly; add a debug print of `spans` and compare against the fixture's exact byte offsets before changing the regex.

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check src/loopmetry/hook_integration_codex.py tests/test_hook_integration_codex.py
git add src/loopmetry/hook_integration_codex.py tests/test_hook_integration_codex.py
git commit -m "feat: add Codex TOML hook-config merge/remove logic"
```

---

### Task 6: Wire `loopmetry integrate codex` into the CLI

**Files:**
- Modify: `src/loopmetry/cli.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 5's `hook_integration_codex.{merge_config, remove_config}`.
- Produces: `loopmetry integrate codex --preview|--apply|--remove [--project-id ...] [--force]` targeting `<root>/.codex/config.toml`, with the same force/backup/diff semantics `_run_integrate` already gives the JSON path.

- [ ] **Step 1: Write the failing CLI tests**

Find `tests/test_cli.py`'s existing `integrate claude-code` tests (search for `"integrate"`) to match its exact fixture/assertion style, then add:

```python
def test_integrate_codex_apply_creates_config_toml(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        exit_code = main(["integrate", "codex", "--root", str(root), "--apply"])
        self.assertEqual(exit_code, 0)
        content = (root / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn("loopmetry capture-hook --source codex", content)

def test_integrate_codex_apply_on_existing_file_requires_force(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_dir = root / ".codex"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('[model]\nname = "gpt-5"\n', encoding="utf-8")
        with self.assertRaises(InputError):
            main(["integrate", "codex", "--root", str(root), "--apply"])

def test_integrate_codex_remove_is_noop_without_existing_file(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        exit_code = main(["integrate", "codex", "--root", str(root), "--remove"])
        self.assertEqual(exit_code, 0)
        self.assertFalse((root / ".codex" / "config.toml").exists())
```

(Import `InputError` from `loopmetry.io` at the top of the test file if not already imported.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_cli -v -k integrate_codex`
Expected: FAIL — `argparse` rejects `"codex"` as a `source` choice for `integrate`.

- [ ] **Step 3: Wire it into `cli.py`**

1. Add the import: `from .hook_integration_codex import merge_config, remove_config`.
2. Change the `integrate` parser's source choices:

```python
    integrate.add_argument("source", choices=("claude-code", "codex"))
```

3. Split `_run_integrate` by source. Replace its body from the existing-file-reading step onward with a dispatch:

```python
def _run_integrate(args: argparse.Namespace) -> int:
    if args.source == "codex":
        return _run_integrate_codex(args)
    return _run_integrate_claude_code(args)


def _run_integrate_claude_code(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    path = root / ".claude" / "settings.local.json"
    existing_text = path.read_text(encoding="utf-8") if path.is_file() else None
    if existing_text is None:
        existing: dict[str, Any] = {}
    else:
        try:
            existing = json.loads(existing_text)
        except json.JSONDecodeError as exc:
            raise InputError(
                f"{path}: existing file is not valid JSON; fix or remove it manually"
            ) from exc
        if not isinstance(existing, dict):
            raise InputError(f"{path}: existing file's top-level JSON value is not an object")

    if args.remove:
        merged, changed = remove_settings(existing)
    else:
        merged, changed = merge_settings(existing, args.project_id)

    old_text = existing_text or ""
    new_text = format_settings(merged) if changed else old_text
    return _finish_integrate(args, path, existing_text, old_text, new_text)


def _run_integrate_codex(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser()
    path = root / ".codex" / "config.toml"
    existing_text = path.read_text(encoding="utf-8") if path.is_file() else None
    old_text = existing_text or ""
    try:
        if args.remove:
            new_text, changed = remove_config(old_text)
        else:
            new_text, changed = merge_config(old_text, args.project_id)
    except ValueError as exc:
        raise InputError(f"{path}: {exc}; fix or remove it manually") from exc
    if not changed:
        new_text = old_text
    return _finish_integrate(args, path, existing_text, old_text, new_text)


def _finish_integrate(
    args: argparse.Namespace,
    path: Path,
    existing_text: str | None,
    old_text: str,
    new_text: str,
) -> int:
    changed = new_text != old_text
    if args.preview:
        if not changed:
            print("no changes needed")
        else:
            diff = difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=str(path) if existing_text is not None else "/dev/null",
                tofile=str(path),
            )
            sys.stdout.writelines(diff)
        return 0

    if not changed:
        print("no changes needed")
        return 0

    if existing_text is not None and not args.force:
        raise InputError(
            f"{path} already exists; pass --force to modify it "
            "(run with --preview first to review the diff)"
        )

    if existing_text is not None:
        backup_path = path.with_name(path.name + ".bak")
        atomic_write_bytes(backup_path, existing_text.encode("utf-8"))
        print(f"backed up existing file to {backup_path}")

    atomic_write_bytes(path, new_text.encode("utf-8"))
    print(f"{'updated' if existing_text is not None else 'created'} {path}")
    return 0
```

Note this factors the shared preview/force/backup/write flow (`_finish_integrate`) out of the original `_run_integrate` body — both sources now call it with their own `(path, existing_text, old_text, new_text)`. Double-check the extracted `_finish_integrate` behaves identically to the original inline logic for the `claude-code` path (run the full existing `integrate claude-code` test suite, not just the new Codex tests, to confirm no regression).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest discover -s tests -v`
Expected: PASS, including every pre-existing `integrate claude-code` test (the refactor in Step 3 must not change that path's behavior).

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check src/loopmetry/cli.py tests/test_cli.py
git add src/loopmetry/cli.py tests/test_cli.py
git commit -m "feat: wire loopmetry integrate codex into the CLI"
```

---

### Task 7: Coverage comparability tests and documentation

**Files:**
- Modify: `tests/test_adapters.py`
- Modify: `docs/hook-capture.md`
- Modify: `docs/roadmap.md`

**Interfaces:**
- Consumes: `ClaudeCodeHistoryAdapter`, `CodexHistoryAdapter` (both already implement `SourceAdapter`).
- Produces: no new runtime code — a regression test plus doc updates.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_adapters.py`:

```python
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
```

Add `from loopmetry.schema import CaptureMode` to the test file's imports if not already present.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run python -m unittest tests.test_adapters -v -k CrossAdapter`
Expected: FAIL only if an import is missing; otherwise this test should already pass mechanically once both adapters exist — that's the point (it's a standing regression guard, not new behavior). If it fails for a real structural mismatch (e.g. `codex_caps.evidence_categories` containing something outside `EVIDENCE_CATEGORIES`), fix `CodexHistoryAdapter.capabilities()` in Task 1/3's code, not the test.

- [ ] **Step 3: Update documentation**

In `docs/hook-capture.md`, update the coverage table's Codex column (the table starting at what's currently around line 173) to reflect what was actually built: historical backfill now supported for `file_changes` (apply_patch) and `human_turns` (user prompts, hashed), `commands` supported but `verifications`/error-derived-from-exit-code explicitly **not** available from history (only from live hook capture) — add a footnote below the table:

```markdown
Codex's historical-backfill adapter (`adapters/codex_history.py`) can extract commands and
apply_patch file changes from rollout files, but Codex's rollout format never persists a
command exit-code or success signal (confirmed against `openai/codex` source) — every
backfilled `command` event's status is `"unknown"`, distinguishing it from live hook capture,
which does get a real exit status. Session attribution is by `session_meta.cwd`, the same
scoping Claude Code's adapter uses; unattributed sessions are excluded, not widened into scope.
```

Also update the "Relationship to historical backfill" section's "(planned)" language for Codex now that both adapters exist, and the `loopmetry integrate <source>` paragraph to mention `codex` is now supported alongside `claude-code`.

In `docs/roadmap.md`, change milestone 2 slice 5's line from the current description to `**Codex parity.** *(implemented)* ...` (matching how slices 1-3 already read), keeping the rest of the sentence describing what was built.

- [ ] **Step 4: Run the full suite**

Run: `uv run python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_adapters.py docs/hook-capture.md docs/roadmap.md
git commit -m "docs: record Codex parity coverage and mark roadmap slice 5 implemented"
```

---

### Task 8: Cross-source (Claude Code + Codex) merge tests

**Files:**
- Modify: `tests/test_adapters.py`

**Interfaces:**
- Consumes: `event_merge.{merge_events, EventConflictError}`, `schema.{Event, CaptureMode}`, `storage.EventStore`.
- Produces: tests only — no new merge code, per the spec's explicit scope boundary (slice 4 owns any `--source auto` orchestration; this only proves the existing generic merge machinery already behaves correctly across sources).

- [ ] **Step 1: Write the tests**

Add to `tests/test_adapters.py`:

```python
class CrossSourceMergeTests(unittest.TestCase):
    def _event(self, event_id: str, source: str, capture_mode, session_id: str = "s1"):
        from datetime import datetime, timezone

        from loopmetry.schema import Actor, Event, EventType

        return Event(
            event_id=event_id,
            project_id="proj",
            session_id=session_id,
            timestamp=datetime(2026, 8, 25, tzinfo=timezone.utc),
            type=EventType.COMMAND,
            actor=Actor.TOOL,
            source=source,
            data={"command": "pytest", "status": "unknown"},
            provenance=(
                ProvenanceRecordFor(source, capture_mode),
            ),
        )

    def test_same_event_id_merges_across_claude_code_and_codex_sources(self) -> None:
        from loopmetry.event_merge import merge_events
        from loopmetry.schema import CaptureMode

        claude_side = self._event("shared-id", "claude-code", CaptureMode.HOOK)
        codex_side = self._event("shared-id", "codex", CaptureMode.HISTORY_BACKFILL)
        merged = merge_events(claude_side, codex_side)
        self.assertEqual(len(merged.provenance), 2)
        sources = {record.source for record in merged.provenance}
        self.assertEqual(sources, {"claude-code", "codex"})

    def test_genuinely_conflicting_cross_source_event_raises(self) -> None:
        from loopmetry.event_merge import EventConflictError, merge_events
        from loopmetry.schema import CaptureMode

        claude_side = self._event("shared-id", "claude-code", CaptureMode.HOOK)
        codex_side = self._event("shared-id", "codex", CaptureMode.HISTORY_BACKFILL)
        codex_side = codex_side.__class__(**{**codex_side.__dict__, "data": {"command": "ruff", "status": "unknown"}})
        with self.assertRaises(EventConflictError):
            merge_events(claude_side, codex_side)

    def test_event_store_ingests_mixed_source_batch_without_duplication(self) -> None:
        import tempfile

        from loopmetry.schema import CaptureMode
        from loopmetry.storage import EventStore

        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            claude_side = self._event("shared-id", "claude-code", CaptureMode.HOOK)
            codex_side = self._event("shared-id", "codex", CaptureMode.HISTORY_BACKFILL)
            result = store.add_events([claude_side, codex_side])
            self.assertEqual(result.inserted, 1)
            self.assertEqual(result.merged, 1)
```

`ProvenanceRecordFor` is a small local helper to add just above the test class:

```python
def ProvenanceRecordFor(source: str, capture_mode) -> "ProvenanceRecord":
    from loopmetry.schema import ProvenanceRecord

    return ProvenanceRecord(source=source, capture_mode=capture_mode, adapter_version="1.0.0")
```

Before writing this, check `storage.EventStore.add_events`'s actual `IngestResult` field semantics (`inserted`/`merged`/`skipped`) against a same-source existing test in `test_storage.py` or `test_adapters.py`'s `HookSourceAdapterTests` (e.g. `test_import_candidates_merges_same_file_duplicate_event_id`) to confirm whether "merge into an existing row" counts as `merged` or `inserted` — adjust the assertion to match the store's actual documented counting convention rather than guessing.

- [ ] **Step 2: Run the tests**

Run: `uv run python -m unittest tests.test_adapters -v -k CrossSource`
Expected: PASS. If `test_event_store_ingests_mixed_source_batch_without_duplication`'s counts don't match, fix the assertion (per the note above), not `EventStore` — this task adds no new merge code.

- [ ] **Step 3: Run the full suite and lint**

```bash
uv run python -m unittest discover -s tests -v
uvx --from ruff==0.12.12 ruff check .
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_adapters.py
git commit -m "test: verify cross-source (Claude Code + Codex) event merge behavior"
```

---

### Task 9: Decision log entry and AGENTS.md routing update

**Files:**
- Modify: `docs/decision-log.md`
- Modify: `AGENTS.md`

**Interfaces:** None — documentation only.

- [ ] **Step 1: Add the decision log entry**

Append to `docs/decision-log.md`, following the existing D-014 entry's structure (Status/Context/Decision/Consequences/Related):

```markdown
## D-016 — Codex hook-config writes via text-templated block replacement, not TOML serialization

**Status:** Accepted
**Context:** Milestone 2 slice 5 (`docs/roadmap.md`) needed `loopmetry integrate codex --preview|--apply|--remove` for `.codex/config.toml`, but no stdlib TOML writer exists (only read-only `tomllib`), and D-014 explicitly deferred this design. Codex's hook `command` field is also a single shell-parsed string with no `args` array, unlike Claude Code's JSON installer's exec-form handlers (confirmed against `docs/hook-capture.md`'s existing example and external Codex hooks documentation).
**Decision:** `hook_integration_codex.py` never re-serializes the whole file. `tomllib` parses the whole file once to validate it's well-formed TOML and that `hooks`/each targeted event's value has the right shape (hard error otherwise, on preview/apply/remove alike, matching D-014's fail-closed rule). Ownership of an existing `[[hooks.<Event>]]` occurrence is decided by parsing just that occurrence's own text span in isolation via `tomllib` (every such span is independently valid TOML on its own) and checking it structurally matches exactly what this installer generates. Writing replaces only the byte spans of owned blocks — appending a fresh block when something changed — leaving every other byte of the file (comments, formatting, unrelated tables) untouched. `--project-id` is embedded via `shlex.quote()` into the single command string rather than passed as a separate argument, since the shell (not `execve`) splits Codex's `command` field.
**Consequences:**

- `merge_config`/`remove_config` take and return raw text, not a parsed structure — different shape from `hook_integration.py`'s dict-based `merge_settings`/`remove_settings`; `cli.py`'s `_run_integrate` dispatches by source into two small source-specific functions that both delegate to a shared `_finish_integrate` for the preview/force/backup/write flow.
- A block whose text doesn't match our exact generated shape (extra keys, a `matcher`-equivalent, hand edits) is never treated as ours and is never touched by `--remove`, mirroring D-014's ownership rule for the JSON installer.
- If Codex's config schema changes to support an exec-form `args` array in the future, this decision (single shell-string + `shlex.quote`) would need a superseding entry, not a silent rewrite.

**Related:** `docs/decision-log.md` D-011, D-014, `docs/hook-capture.md`, `docs/roadmap.md` milestone 2 slice 5, `src/loopmetry/hook_integration_codex.py`, `src/loopmetry/cli.py`
```

- [ ] **Step 2: Update `AGENTS.md`'s routing table**

Extend the existing "Source adapters and historical backfill" and "Hook integration installer" rows (or add new ones) to list `codex_history.py` and `hook_integration_codex.py`:

```markdown
| Source adapters and historical backfill | `docs/architecture.md`, `docs/hook-capture.md`, `docs/decision-log.md` D-011, D-013 | `src/loopmetry/adapters/`, `src/loopmetry/adapters/claude_code_history.py`, `src/loopmetry/adapters/codex_history.py`, `src/loopmetry/hook_capture.py`, `src/loopmetry/minimize.py` | `tests/test_adapters.py`, `tests/test_claude_code_history.py`, `tests/test_codex_history.py`, `tests/test_hook_capture.py` |
| Hook integration installer | `docs/hook-capture.md`, `docs/decision-log.md` D-014, D-016 | `src/loopmetry/hook_integration.py`, `src/loopmetry/hook_integration_codex.py`, `src/loopmetry/cli.py` | `tests/test_hook_integration.py`, `tests/test_hook_integration_codex.py`, `tests/test_cli.py` |
```

- [ ] **Step 3: Verify and commit**

```bash
uv run python -m unittest discover -s tests -v
uvx --from ruff==0.12.12 ruff check .
uv lock --check
git add docs/decision-log.md AGENTS.md
git commit -m "docs: add D-016 (Codex TOML merge design) and update AGENTS.md routing"
```

---

## Final verification (run once all tasks are complete)

```bash
uv sync --locked
uv lock --check
uv run python -m unittest discover -s tests -v
uvx --from ruff==0.12.12 ruff check .
uv run loopmetry run --input examples/demo_project.jsonl --assignment-id demo --submitter-id local
uv build
```
