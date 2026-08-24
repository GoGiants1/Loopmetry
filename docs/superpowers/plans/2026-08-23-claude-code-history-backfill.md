# Claude Code Historical Backfill (Milestone 2, Slice 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A consented, bounded, incremental `loopmetry history discover|preview|import --source claude-code` flow that converts existing local Claude Code session transcripts into canonical Loopmetry events with `history-backfill` provenance.

**Architecture:** A `ClaudeCodeHistoryAdapter` implements the slice-1 `SourceAdapter` contract. Discovery is bounded to `~/.claude/projects/<encoded-project-root>/` for the current project only; every candidate is confirmed from content (session `cwd`), never from the lossy directory name alone. Parsing streams JSONL records, pairs `tool_use`/`tool_result` blocks, reuses the hook path's minimization helpers (extracted to a shared module first), counts unknown records as diagnostics, and writes canonical events to `.loopmetry/events/claude-code-history.jsonl` where the existing participant workflow already discovers them. Checkpoints make re-import incremental and idempotent, and — per D-013 — also carry unresolved `tool_use`/`tool_result` pairing state across import boundaries so a Bash call's outcome is written under its event ID at most once, only after it is either observed or confirmed stalled.

**Tech Stack:** Python ≥3.12 stdlib only. Depends on slice 1 (`src/loopmetry/adapters/base.py`, `checkpoints.py`, `Event.provenance`, `CaptureMode.HISTORY_BACKFILL`) being merged.

## Global Constraints

- Everything in the slice-1 plan's Global Constraints applies (uv, unittest, ruff 0.12.12, stdlib-only, repo style).
- Invariant 10 (`AGENTS.md`): bounded scope, preview, explicit consent; non-interactive runs never read history implicitly. Running `loopmetry history import` is itself the explicit consent act; `loopmetry run` does NOT gain history access in this slice (that is slice 4's `--include-history`).
- Invariant 5 / `PRIVACY.md`: no raw prompts, response text, source bodies, full command lines, or absolute private paths in emitted events. Reuse the hook path's minimization (path relativization + redaction, command label + sha256, prompt sha256 + length).
- Transcripts are read streaming and never copied; only canonical events are written, under `.loopmetry/`. Real user transcripts are never committed as test fixtures — tests build synthetic transcripts in temp dirs.
- Unattributed/unparsed/oversized input becomes an explicit `Diagnostic`, never a silent drop (invariant 4); caps degrade coverage to `partial` instead of aborting.
- Emitted events use `source="claude-code"` (same value as hook capture) — the capture mode in provenance is what distinguishes backfill from hooks.
- Deterministic event IDs: re-importing the same transcript yields byte-identical events.

## Design inputs (field research on existing session stores — local notes at `.loopmetry/notes/history-backfill-research.md`, deliberately uncommitted)

- Sessions live at `~/.claude/projects/<encoded-cwd>/*.jsonl`; subagent transcripts under `<session>/subagents/` (out of scope this slice — count them as a `note`-free diagnostic instead of importing).
- The directory encoding replaces **both `/` and `.`** with `-` and is lossy → encode-and-match only; confirm from content.
- Session→cwd attribution order: `sessions-index.json` (`originalPath`, two shapes: bare array or `{version, entries}`) → first non-`queue-operation` record's `cwd` → unattributable (diagnostic, candidate excluded).
- Record-type drift is normal: skip-with-count unknown `type` values (`queue-operation`, `file-history-snapshot`, `summary`, …); one malformed line must never abort a session.
- Cap pathological inputs (existing tooling in the wild caps tool results at a few thousand chars and times out git subprocesses); we cap record line length and skip-with-diagnostic.

---

### Task 1: Extract shared minimization helpers

**Files:**
- Create: `src/loopmetry/minimize.py`
- Modify: `src/loopmetry/hook_capture.py`
- Test: existing suite (behavior-preserving refactor; `tests/test_hook_capture.py` is the guard)

**Interfaces:**
- Produces public helpers (moved, not rewritten — bodies come verbatim from `src/loopmetry/hook_capture.py`):
  - `minimize.hash_text(value: str) -> str` (from `_hash_text`, line 42)
  - `minimize.canonical_hash(value: object) -> str` (from `_canonical_hash`, line 46)
  - `minimize.safe_identifier(value: str, *, fallback: str) -> str` (from `_safe_identifier`, line 51)
  - `minimize.derive_project_id(cwd: str) -> str` (from `derive_project_id`, line 56)
  - `minimize.safe_relative_path(value: object, cwd: str) -> str | None` (from `_safe_path`, line 65)
  - `minimize.command_signature(command: str) -> tuple[str, str | None]` (from `_command_signature`, line 205, plus its regex constants and the `shlex`/`os` fallback)

- [ ] **Step 1: Move the helpers**

Create `src/loopmetry/minimize.py` with module docstring `"""Shared content-minimization helpers used by every source adapter."""`, the imports they need (`hashlib`, `json`, `os`, `re`, `shlex`, `pathlib` names), the constants `_PATCH_FILE_RE` stays in hook_capture (patch parsing is hook-specific), but move `_SAFE_ID_RE` and the command-signature rule table. Rename per the interface list above (drop leading underscores).

In `src/loopmetry/hook_capture.py`, delete the moved definitions and import instead:

```python
from .minimize import (
    canonical_hash as _canonical_hash,
    command_signature as _command_signature,
    derive_project_id,
    hash_text as _hash_text,
    safe_identifier as _safe_identifier,
    safe_relative_path as _safe_path,
)
```

(keeping the `derive_project_id` re-export so `from loopmetry.hook_capture import derive_project_id` continues to work for the CLI and tests).

- [ ] **Step 2: Run the full suite to prove behavior is unchanged**

Run: `uv run python -m unittest discover -s tests -v`
Expected: PASS with the same test count as before the change.

- [ ] **Step 3: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check .
git add src/loopmetry/minimize.py src/loopmetry/hook_capture.py
git commit -m "refactor: extract shared minimization helpers for source adapters"
```

---

### Task 2: Bounded session discovery

**Files:**
- Create: `src/loopmetry/adapters/claude_code_history.py`
- Test: `tests/test_claude_code_history.py`

**Interfaces:**
- Consumes: `DiscoveryContext`, `SourceCandidate`, `ImportPreview`, `AdapterCapabilities`, `Diagnostic`, `AdapterError` (slice 1); `CaptureMode` (schema).
- Produces:
  - `CLAUDE_HISTORY_ADAPTER_VERSION = "1.0.0"`
  - `encode_claude_project_dir(project_root: Path) -> str` — absolute resolved path with every `/` and `.` replaced by `-` (e.g. `/Users/w/my.app` → `-Users-w-my-app`).
  - `class ClaudeCodeHistoryAdapter` with `name = "claude-code-history"`, `adapter_version = CLAUDE_HISTORY_ADAPTER_VERSION`, constructor `def __init__(self, claude_home: Path | None = None)` (defaults to `Path.home() / ".claude"`; injectable for tests), and the contract methods. `discover` also records `self.last_discovery_diagnostics: tuple[Diagnostic, ...]` (unattributable candidates, skipped subagent dirs).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_claude_code_history.py`:

```python
"""Tests for the Claude Code historical-backfill adapter (synthetic transcripts only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from loopmetry.adapters.base import DiscoveryContext
from loopmetry.adapters.claude_code_history import (
    ClaudeCodeHistoryAdapter,
    encode_claude_project_dir,
)


def _record(record_type: str, **extra: object) -> dict:
    base: dict = {
        "type": record_type,
        "sessionId": "sess-0001",
        "timestamp": "2026-08-20T09:00:00Z",
        "cwd": extra.pop("cwd", "/work/project"),
        "version": "2.0.0",
    }
    base.update(extra)
    return base


def _write_session(project_dir: Path, name: str, records: list[dict]) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / name
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


class EncodingTests(unittest.TestCase):
    def test_slashes_and_dots_become_dashes(self) -> None:
        self.assertEqual(
            encode_claude_project_dir(Path("/Users/w/my.app")), "-Users-w-my-app"
        )


class DiscoveryTests(unittest.TestCase):
    def _setup(self, tmp: str) -> tuple[ClaudeCodeHistoryAdapter, DiscoveryContext, Path]:
        root = Path(tmp) / "work" / "project"
        root.mkdir(parents=True)
        claude_home = Path(tmp) / "claude-home"
        project_dir = claude_home / "projects" / encode_claude_project_dir(root)
        adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
        context = DiscoveryContext(project_root=root)
        return adapter, context, project_dir

    def test_no_claude_home_discovers_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, _ = self._setup(tmp)
            self.assertEqual(adapter.discover(context), ())

    def test_discovers_sessions_whose_cwd_matches_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, project_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_session(project_dir, "b.jsonl", [_record("user", cwd=root)])
            _write_session(project_dir, "a.jsonl", [_record("user", cwd=root)])
            _write_session(
                project_dir, "other.jsonl", [_record("user", cwd="/somewhere/else")]
            )
            candidates = adapter.discover(context)
            self.assertEqual([c.label for c in candidates], ["a.jsonl", "b.jsonl"])
            self.assertEqual(candidates[0].source, "claude-code-history")
            self.assertEqual(candidates[0].session_id, "sess-0001")
            kinds = [d.kind for d in adapter.last_discovery_diagnostics]
            self.assertIn("unattributed_session", kinds)

    def test_skips_queue_operation_lines_when_reading_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, project_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_session(
                project_dir,
                "a.jsonl",
                [{"type": "queue-operation"}, _record("user", cwd=root)],
            )
            self.assertEqual(len(adapter.discover(context)), 1)

    def test_since_filters_by_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter, context, project_dir = self._setup(tmp)
            root = str(context.project_root)
            _write_session(project_dir, "a.jsonl", [_record("user", cwd=root)])
            future = DiscoveryContext(
                project_root=context.project_root,
                since=datetime(2999, 1, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(adapter.discover(future), ())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_claude_code_history -v`
Expected: ERROR `ModuleNotFoundError: No module named 'loopmetry.adapters.claude_code_history'`.

- [ ] **Step 3: Implement discovery**

Create `src/loopmetry/adapters/claude_code_history.py`:

```python
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
from typing import Any, Iterator, Mapping, Sequence

from ..minimize import (
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


def encode_claude_project_dir(project_root: Path) -> str:
    resolved = str(Path(project_root).expanduser().resolve())
    return resolved.replace("/", "-").replace(".", "-")


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
```

with the module-level helper:

```python
def _cwd_in_scope(cwd: str, project_root: Path) -> bool:
    try:
        candidate = Path(cwd).expanduser().resolve()
    except OSError:
        return False
    return candidate == project_root or project_root in candidate.parents
```

(`import_candidates` is Task 3 — add a temporary `raise NotImplementedError` body so the class is importable, and remove it in Task 3.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m unittest tests.test_claude_code_history -v`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check .
git add src/loopmetry/adapters/claude_code_history.py tests/test_claude_code_history.py
git commit -m "feat: bounded discovery of Claude Code history sessions"
```

---

### Task 3: Transcript parsing into canonical events

**Files:**
- Modify: `src/loopmetry/adapters/claude_code_history.py`
- Test: `tests/test_claude_code_history.py` (extend)

**Interfaces:**
- Consumes: Task 2 class; `Event.from_mapping`; minimization helpers.
- Produces: `ClaudeCodeHistoryAdapter.import_candidates(candidates, context, checkpoint=None) -> AdapterRun`. Event mapping (all events: `source="claude-code"`, provenance `[{source: "claude-code", capture_mode: "history-backfill", adapter_version: "1.0.0", source_ref: {"session_file": <file name only>, "record_index": <int>}}]`, `project_id = derive_project_id(str(context.project_root))`, `session_id` from the record, timestamp from the record):

| Transcript observation | Canonical event |
|---|---|
| `type: "user"`, `message.content` is a string (or list of only `text` blocks), no `isMeta` | `human_intervention` `{action: "prompt", summary: "User submitted a prompt; content omitted.", prompt_sha256, prompt_length}`, actor `human` |
| assistant `tool_use` named `Read`/`ReadFile` with `file_path` | `file_read` `{path: <relativized>}`, actor `agent` |
| assistant `tool_use` named `Edit`/`Write`/`MultiEdit`/`NotebookEdit` with `file_path`/`notebook_path` | `file_change` `{path, action: "add"` when the tool is `Write` else `"modify"}`, actor `agent` |
| assistant `tool_use` named `Bash` with `command`, paired with its later `tool_result` (`is_error` flag) | `command` `{command: <signature label>, status: "failed"` if `is_error` else `"success"`, command_sha256, tool_name: "Bash"}`, actor `tool`; plus `verification` / `error` events exactly as the hook path derives them (same signature table, same status mapping) |
| assistant `tool_use` named `ExitPlanMode`/`TodoWrite`/`EnterPlanMode` | `plan` `{summary: "Agent created or updated a plan; plan text omitted."}`, actor `agent` |
| unmatched Bash `tool_use` with no `tool_result` by end of session | **no event yet.** Stashed in the checkpoint's `pending` map (see D-013) and carried into the next import. Only finalized to `command(status="unknown")` — using the entry's originally-stored `record_index` so the event ID never changes — once an import observes the session has stalled (zero file growth since the position where the entry was left); a `stalled_tool_call` diagnostic is emitted alongside. While merely pending (not yet stalled), an `unresolved_tool_call` diagnostic is emitted instead, and no event is written. Other unmatched, non-Bash tools are dropped silently — they produced no evidence and have no result to pair against. |
| any other record `type` | counted into one `unparsed_record`-style diagnostic per type: kind `"skipped_record_type"`, summary naming the type |
| malformed JSON line | diagnostic kind `"unparsed_record"` |
| line longer than `_MAX_RECORD_BYTES` | diagnostic kind `"truncated_input"`; coverage for `commands`/`file_changes` degrades to `PARTIAL` |

Event IDs: `f"hist-{canonical_hash({'session': session_id, 'file': file_name, 'index': record_index, 'kind': kind, 'suffix': suffix})[:24]}"` — deterministic across re-imports, using the record's original index even when the event is emitted on a later import (stalled Bash finalization). Coverage: categories from `capabilities()` at `FULL`, downgraded to `PARTIAL` when any `truncated_input`/`unparsed_record`/`unresolved_tool_call`/`stalled_tool_call` diagnostics occurred; `requirements` and `commits` are absent (not claimable from transcripts alone).

**Checkpoint-boundary tool_use/tool_result pairing (D-013):** because Bash `command` events depend on pairing a `tool_use` with a `tool_result` that may arrive in a later append, and re-emitting a corrected event under an already-written `event_id` would raise `EventConflictError` on merge, an event's content is only ever written once — after the outcome is either observed or the session is confirmed stalled. See D-013 in `docs/decision-log.md` for the full rationale; the mechanics are in Tasks 3 and 4 below.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_code_history.py` (uses the existing `_record`/`_write_session` helpers; note assistant/user record shapes):

```python
def _assistant_tool_use(name: str, tool_input: dict, tool_use_id: str, cwd: str) -> dict:
    return _record(
        "assistant",
        cwd=cwd,
        message={
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": tool_input}
            ],
        },
    )


def _user_tool_result(tool_use_id: str, cwd: str, is_error: bool = False) -> dict:
    return _record(
        "user",
        cwd=cwd,
        message={
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "is_error": is_error}
            ],
        },
    )


class ImportTests(unittest.TestCase):
    def _import(self, records: list[dict]):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            for record in records:
                record["cwd"] = str(root)
            _write_session(project_dir, "sess.jsonl", records)
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            return adapter.import_candidates(adapter.discover(context), context)

    def test_prompt_becomes_hashed_human_intervention(self) -> None:
        run = self._import(
            [_record("user", message={"role": "user", "content": "please fix the bug"})]
        )
        events = [e for e in run.events if e.type.value == "human_intervention"]
        self.assertEqual(len(events), 1)
        self.assertNotIn("please fix the bug", json.dumps(events[0].to_mapping()))
        self.assertEqual(events[0].data["prompt_length"], len("please fix the bug"))
        record = events[0].provenance[0]
        self.assertEqual(record.capture_mode.value, "history-backfill")
        self.assertEqual(record.source_ref["session_file"], "sess.jsonl")

    def test_read_and_edit_become_file_events_with_relative_paths(self) -> None:
        cwd_marker = "__CWD__"
        records = [
            _assistant_tool_use("Read", {"file_path": cwd_marker + "/src/a.py"}, "t1", ""),
            _assistant_tool_use("Edit", {"file_path": cwd_marker + "/src/a.py"}, "t2", ""),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            for record in records:
                record["cwd"] = str(root)
                block = record["message"]["content"][0]
                block["input"]["file_path"] = block["input"]["file_path"].replace(
                    cwd_marker, str(root)
                )
            _write_session(project_dir, "sess.jsonl", records)
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            run = adapter.import_candidates(adapter.discover(context), context)
        types = sorted(e.type.value for e in run.events)
        self.assertEqual(types, ["file_change", "file_read"])
        for event in run.events:
            self.assertEqual(event.data["path"], "src/a.py")

    def test_bash_test_command_yields_command_verification_and_error(self) -> None:
        run = self._import(
            [
                _assistant_tool_use(
                    "Bash", {"command": "uv run python -m unittest"}, "t1", ""
                ),
                _user_tool_result("t1", "", is_error=True),
            ]
        )
        by_type = {e.type.value: e for e in run.events}
        self.assertEqual(by_type["command"].data["status"], "failed")
        self.assertNotIn("uv run", json.dumps(by_type["command"].to_mapping()["data"]))
        self.assertEqual(by_type["verification"].data["status"], "failed")
        self.assertIn("error", by_type)

    def test_unknown_record_types_become_diagnostics_not_events(self) -> None:
        run = self._import(
            [
                _record("file-history-snapshot"),
                _record("user", message={"role": "user", "content": "hi"}),
            ]
        )
        kinds = {d.kind for d in run.diagnostics}
        self.assertIn("skipped_record_type", kinds)
        self.assertEqual(len(run.events), 1)

    def test_reimport_is_deterministic(self) -> None:
        records = [_record("user", message={"role": "user", "content": "hi"})]
        first = self._import([dict(records[0])])
        second = self._import([dict(records[0])])
        self.assertEqual(
            [e.to_mapping() for e in first.events],
            [e.to_mapping() for e in second.events],
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_claude_code_history -v`
Expected: FAIL with `NotImplementedError` from `import_candidates`.

- [ ] **Step 3: Implement parsing**

Replace the `NotImplementedError` body. Structure (full code, following the event table above):

```python
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
            reset = bool(previous_position) and start_index == 0
            previous_records_read = 0 if reset else (previous_position or {}).get("records_read", 0)
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
            positions[candidate.candidate_id] = session.position()
        diagnostics = tuple(
            Diagnostic(kind=kind, summary=summary, count=count)
            for (kind, summary), count in sorted(diagnostic_counts.items())
        )
        degraded = any(d.kind in {"unparsed_record", "truncated_input"} for d in diagnostics)
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
```

`_SessionParser` is a small class in the same module holding the pairing state. Per D-013, it never emits a Bash `command` event speculatively — only once the outcome is observed, or once the session is confirmed stalled across an import boundary — so an `event_id` is written at most once with content that never changes afterward.

- Constructor: `__init__(self, *, path, project_root, project_id, start_index, pending_seed: Mapping[str, Mapping[str, Any]])`. Copies `pending_seed` into `self.pending: dict[str, dict[str, Any]]` (keyed by `tool_use_id`, each entry `{"record_index": int, "command": str, "timestamp": str}`) instead of starting empty — this is what lets a `tool_result` arriving in a later append still find the `tool_use` it belongs to.
- `parse()` streams lines with `enumerate`, skipping indexes below `start_index`; per line: length check (`len(line.encode("utf-8", errors="replace")) > _MAX_RECORD_BYTES` → count `("truncated_input", "oversized transcript record skipped")` and continue), `json.loads` failure → count `("unparsed_record", "malformed JSON line")`, non-`user`/`assistant` `type` → count `("skipped_record_type", f"records of type {record_type!r} are not imported")`.
- User records: content string or all-`text`-block list and no `isMeta` → emit `human_intervention` with `hash_text(prompt)`/`len(prompt)`. Content list with `tool_result` blocks → for each block, pop `self.pending.get(tool_use_id)` (present either because this parser just stashed it, or because it was restored from `pending_seed`); if found and it is a Bash entry, emit the command/verification/error events with status `"failed"` if `is_error` else `"success"`, using the entry's stored `record_index` (not the current line index) for the event ID. If not found (already finalized as stalled in an earlier import, or never a Bash call), the `tool_result` is dropped — it has nothing left to pair with.
- Assistant records: iterate `message.content` `tool_use` blocks. `Read`-like → emit `file_read` immediately (path via `safe_relative_path(value, str(self.project_root))`; unextractable path → count `("unextractable_path", ...)`). Edit-like → emit `file_change` (`action="add"` for `Write`, else `"modify"`). Plan tools → emit `plan`. `Bash` → stash `{"record_index": index, "command": command, "timestamp": timestamp}` in `self.pending[tool_use_id]`; emit nothing yet.
- End of stream: `self.pending` is deliberately left as-is — `parse()` does **not** flush it. Finalization is a separate, explicit step (`finalize_stalled`) so it can compare against the checkpoint's growth history rather than assuming "end of this import" means "end of the session."
- `finalize_stalled(self, *, previous_records_read: int) -> list[Event]`: for each entry still in `self.pending` whose `record_index < previous_records_read` (i.e., it was already pending *before* this import started, not newly added by this import) — meaning at least one full import cycle passed with the file never growing past that point — emit its `command(status="unknown")` event (plus no verification/error; unknown status has nothing to verify), count `("stalled_tool_call", "a Bash call's result never arrived; session appears stalled")`, and remove it from `self.pending`. Any entry that fails this check (added fresh in this import, i.e. `record_index >= previous_records_read`) stays in `self.pending` untouched, and instead counts `("unresolved_tool_call", "a Bash call is awaiting its result")`.
- Command/verification/error derivation mirrors `hook_capture` exactly: `label, kind = command_signature(command)`; command data `{"command": label, "status": status, "command_sha256": hash_text(command), "tool_name": "Bash"}`; verification only when `kind` is not None with status map `{"success": "passed", "failed": "failed", "unknown": "skipped"}`; error event `{"code": "TOOL_EXIT_NONZERO", "message": f"{label} failed; output omitted."}` only when status is `"failed"`.
- Every event is built through one `_event(...)` helper that fills the envelope: deterministic `event_id` (formula in the Interfaces block), `source=_EVENT_SOURCE`, `provenance` with `source_ref={"session_file": self.path.name, "record_index": index}`, timestamp from the record (fall back to the previous record's timestamp; if the first record has none, count `("unparsed_record", "record missing timestamp")` and skip), constructed via `Event.from_mapping` so schema validation applies.
- `position()` returns `{"content_sha256": <sha256 of the first line>, "records_read": <total line count seen>, "pending": <self.pending, JSON-serializable as-is>}`; `_resume_index(saved, path, counts)` returns `saved.get("records_read", 0)` when the stored `content_sha256` still matches the file's current first line, else counts `("checkpoint_reset", "transcript rotated or replaced; re-importing from the start")` and returns 0 (a reset also drops `pending_seed` for that candidate — a rotated file's line indexes no longer mean anything, so any old pending state is stale and must not be restored).

- [ ] **Step 4: Run tests to verify they pass, then the full suite**

Run: `uv run python -m unittest tests.test_claude_code_history -v && uv run python -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 5: Lint and commit**

```bash
uvx --from ruff==0.12.12 ruff check .
git add src/loopmetry/adapters/claude_code_history.py tests/test_claude_code_history.py
git commit -m "feat: parse Claude Code transcripts into canonical backfill events"
```

---

### Task 4: Incremental import test (checkpoint behavior end-to-end)

**Files:**
- Test: `tests/test_claude_code_history.py` (extend; implementation already landed in Task 3 — this task proves incrementality and rotation explicitly and fixes anything it flushes out)

- [ ] **Step 1: Write the tests**

```python
class IncrementalImportTests(unittest.TestCase):
    def test_second_import_with_checkpoint_only_reads_new_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            first_record = _record(
                "user", cwd=str(root), message={"role": "user", "content": "one"}
            )
            path = _write_session(project_dir, "sess.jsonl", [first_record])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            run_one = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual(len(run_one.events), 1)

            second_record = _record(
                "user",
                cwd=str(root),
                timestamp="2026-08-20T10:00:00Z",
                message={"role": "user", "content": "two"},
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(second_record) + "\n")
            run_two = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_one.checkpoint
            )
            self.assertEqual(len(run_two.events), 1)
            self.assertNotEqual(
                run_one.events[0].event_id, run_two.events[0].event_id
            )

    def test_pending_bash_call_resolved_by_later_append_yields_one_correct_event(self) -> None:
        """D-013: a tool_use at the checkpoint boundary must not lose its real result."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            tool_use = _assistant_tool_use(
                "Bash", {"command": "uv run pytest"}, "t1", ""
            )
            tool_use["cwd"] = str(root)
            path = _write_session(project_dir, "sess.jsonl", [tool_use])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)

            run_one = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual([e.type.value for e in run_one.events], [])
            self.assertIn("unresolved_tool_call", {d.kind for d in run_one.diagnostics})

            result = _user_tool_result("t1", "", is_error=False)
            result["cwd"] = str(root)
            result["timestamp"] = "2026-08-20T09:00:01Z"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result) + "\n")
            run_two = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_one.checkpoint
            )
            by_type = {e.type.value: e for e in run_two.events}
            self.assertEqual(by_type["command"].data["status"], "success")
            self.assertNotIn("stalled_tool_call", {d.kind for d in run_two.diagnostics})

    def test_stalled_bash_call_finalizes_to_unknown_only_after_no_growth(self) -> None:
        """D-013: only finalize to unknown once an import cycle shows the file stopped growing."""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            tool_use = _assistant_tool_use(
                "Bash", {"command": "uv run pytest"}, "t1", ""
            )
            tool_use["cwd"] = str(root)
            _write_session(project_dir, "sess.jsonl", [tool_use])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)

            run_one = adapter.import_candidates(adapter.discover(context), context)
            self.assertEqual([e.type.value for e in run_one.events], [])

            # Nothing appended: the file has not grown since run_one left this entry pending.
            run_two = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_one.checkpoint
            )
            by_type = {e.type.value: e for e in run_two.events}
            self.assertEqual(by_type["command"].data["status"], "unknown")
            self.assertIn("stalled_tool_call", {d.kind for d in run_two.diagnostics})

            # A third import must not re-emit or conflict with the now-finalized event.
            run_three = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_two.checkpoint
            )
            self.assertEqual(run_three.events, ())

    def test_rotated_transcript_resets_checkpoint_with_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "work" / "project"
            root.mkdir(parents=True)
            claude_home = Path(tmp) / "claude-home"
            project_dir = claude_home / "projects" / encode_claude_project_dir(root)
            record = _record(
                "user", cwd=str(root), message={"role": "user", "content": "one"}
            )
            _write_session(project_dir, "sess.jsonl", [record])
            adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
            context = DiscoveryContext(project_root=root)
            run_one = adapter.import_candidates(adapter.discover(context), context)

            replacement = _record(
                "user",
                cwd=str(root),
                timestamp="2026-08-21T09:00:00Z",
                message={"role": "user", "content": "different first line"},
            )
            _write_session(project_dir, "sess.jsonl", [replacement])
            run_two = adapter.import_candidates(
                adapter.discover(context), context, checkpoint=run_one.checkpoint
            )
            self.assertEqual(len(run_two.events), 1)
            self.assertIn("checkpoint_reset", {d.kind for d in run_two.diagnostics})
```

- [ ] **Step 2: Run, fix anything these reveal, and commit**

Run: `uv run python -m unittest tests.test_claude_code_history -v`
Expected: PASS (Task 3 already implements this; if any test fails, fix `_resume_index`/`position()`/`finalize_stalled` until green — do not weaken the tests). The four new tests together prove D-013's guarantee end-to-end: a pending Bash call survives a checkpoint boundary and resolves correctly when its result arrives late, finalizes to `unknown` only after a full no-growth import cycle, and is never re-emitted or re-conflicted once finalized.

```bash
git add tests/test_claude_code_history.py src/loopmetry/adapters/claude_code_history.py
git commit -m "test: prove incremental and rotation-safe history import"
```

---

### Task 5: `loopmetry history` CLI

**Files:**
- Modify: `src/loopmetry/cli.py` (parser at `_build_parser`, `src/loopmetry/cli.py:205`; dispatch in `main`, line 590)
- Test: `tests/test_cli.py` (extend)

**Interfaces:**
- Consumes: `ClaudeCodeHistoryAdapter`, `DiscoveryContext`, `load_checkpoint`/`save_checkpoint` (slice 1 Task 3), `load_jsonl` (`src/loopmetry/io.py:16`).
- Produces CLI contract (documented in `docs/hook-capture.md` and `docs/submission-workflow.md` by Task 6):
  - `loopmetry history discover --source claude-code [--root DIR] [--since YYYY-MM-DD] [--json]` — prints one line per candidate (`label`, `session_id`, size, modified time) or a JSON array with `--json`; exit 0 with `"no sessions found"` on empty.
  - `loopmetry history preview --source claude-code [--root DIR] [--since YYYY-MM-DD] [--json]` — candidate list plus totals (sessions, bytes) and discovery diagnostics. Read-only; never writes.
  - `loopmetry history import --source claude-code [--root DIR] [--since YYYY-MM-DD] [--output PATH] [--yes]` — interactive TTY: print the preview, then require `y` on stdin; non-TTY/non-interactive: require `--yes`, else exit 2 with a message naming the flag (running `import --yes` IS the explicit consent — never triggered implicitly). Writes the union of existing and new events (deduplicated by `event_id`, sorted by `(timestamp, event_id)`) to `--output` (default `<root>/.loopmetry/events/claude-code-history.jsonl`), saves the checkpoint via `save_checkpoint`, prints an import summary: events written, per-kind diagnostic counts, coverage. A corrupt checkpoint (`AdapterError` from `load_checkpoint`) is reported and treated as no checkpoint.
  - Only `--source claude-code` is accepted (choices list); Codex arrives in slice 5.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (match its existing invocation style — it calls `loopmetry.cli.main([...])` and captures stdout; reuse the synthetic-transcript helpers by importing from `tests.test_claude_code_history` or duplicating the two small builders locally):

```python
class HistoryCliTests(unittest.TestCase):
    def _make_history(self, tmp: Path) -> Path:
        root = tmp / "work" / "project"
        root.mkdir(parents=True)
        claude_home = tmp / "claude-home"
        project_dir = claude_home / "projects" / encode_claude_project_dir(root)
        project_dir.mkdir(parents=True)
        record = {
            "type": "user",
            "sessionId": "sess-1",
            "timestamp": "2026-08-20T09:00:00Z",
            "cwd": str(root),
            "message": {"role": "user", "content": "hello"},
        }
        (project_dir / "sess.jsonl").write_text(
            json.dumps(record) + "\n", encoding="utf-8"
        )
        self.claude_home = claude_home
        return root

    def test_discover_lists_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_history(Path(tmp))
            with mock.patch.dict(
                os.environ, {"LOOPMETRY_CLAUDE_HOME": str(self.claude_home)}
            ):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        ["history", "discover", "--source", "claude-code", "--root", str(root)]
                    )
        self.assertEqual(exit_code, 0)
        self.assertIn("sess.jsonl", stdout.getvalue())

    def test_import_requires_consent_when_not_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_history(Path(tmp))
            with mock.patch.dict(
                os.environ, {"LOOPMETRY_CLAUDE_HOME": str(self.claude_home)}
            ):
                exit_code = main(
                    ["history", "import", "--source", "claude-code", "--root", str(root)]
                )
        self.assertEqual(exit_code, 2)
        self.assertFalse((root / ".loopmetry" / "events").exists())

    def test_import_with_yes_writes_events_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_history(Path(tmp))
            with mock.patch.dict(
                os.environ, {"LOOPMETRY_CLAUDE_HOME": str(self.claude_home)}
            ):
                exit_code = main(
                    [
                        "history", "import", "--source", "claude-code",
                        "--root", str(root), "--yes",
                    ]
                )
        self.assertEqual(exit_code, 0)
        output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
        events = load_jsonl(output)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].provenance[0].capture_mode.value, "history-backfill")
        checkpoint = root / ".loopmetry" / "checkpoints" / "claude-code-history.json"
        self.assertTrue(checkpoint.exists())

    def test_import_twice_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._make_history(Path(tmp))
            with mock.patch.dict(
                os.environ, {"LOOPMETRY_CLAUDE_HOME": str(self.claude_home)}
            ):
                args = [
                    "history", "import", "--source", "claude-code",
                    "--root", str(root), "--yes",
                ]
                main(args)
                main(args)
        output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
        self.assertEqual(len(load_jsonl(output)), 1)
```

(`LOOPMETRY_CLAUDE_HOME` is a test-focused env override for the Claude home directory — add it to the CLI handler as `Path(os.environ.get("LOOPMETRY_CLAUDE_HOME", Path.home() / ".claude"))`; it also gives administrators a documented escape hatch for non-standard installs.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m unittest tests.test_cli -v`
Expected: FAIL (`SystemExit`/argparse error: `invalid choice: 'history'`).

- [ ] **Step 3: Implement the CLI**

In `_build_parser` add:

```python
    history = subparsers.add_parser(
        "history",
        help="Discover, preview, and import existing local agent sessions (consented backfill).",
    )
    history_subparsers = history.add_subparsers(dest="history_command", required=True)
    for verb, help_text in (
        ("discover", "List importable sessions for this project."),
        ("preview", "Show what an import would read, without importing."),
        ("import", "Import sessions into canonical events (requires consent)."),
    ):
        verb_parser = history_subparsers.add_parser(verb, help=help_text)
        verb_parser.add_argument("--source", required=True, choices=["claude-code"])
        verb_parser.add_argument("--root", default=".")
        verb_parser.add_argument("--since", default=None, help="YYYY-MM-DD lower bound")
        if verb == "import":
            verb_parser.add_argument("--output", default=None)
            verb_parser.add_argument(
                "--yes",
                action="store_true",
                help="Consent to reading local Claude Code transcripts non-interactively.",
            )
        else:
            verb_parser.add_argument("--json", action="store_true")
```

Add a `_run_history(args)` handler implementing the Interfaces contract: build `DiscoveryContext(project_root=Path(args.root), since=_parse_since(args.since), interactive=sys.stdin.isatty())`, instantiate `ClaudeCodeHistoryAdapter(claude_home=...)` with the env override, then dispatch on `args.history_command`. `_parse_since` parses `YYYY-MM-DD` into a UTC-midnight datetime and raises a CLI error otherwise. Import path: consent gate first (TTY prompt printing `preview.session_count` and `preview.total_size_bytes` then reading `input()`; non-TTY requires `--yes`), `load_checkpoint` (catching `AdapterError` → print warning, use `None`), `import_candidates`, merge-with-existing via `load_jsonl` on the output file when it exists (catch `InputError` for a missing/empty file), dedupe by `event_id` preferring existing, sort, write atomically with the same pattern as `save_checkpoint`, then `save_checkpoint(root, run.checkpoint)` and print the summary. Wire `_run_history` into `main`'s command dispatch next to the existing commands.

- [ ] **Step 4: Run the full suite and lint**

Run: `uv run python -m unittest discover -s tests -v && uvx --from ruff==0.12.12 ruff check .`
Expected: PASS / clean.

- [ ] **Step 5: Commit**

```bash
git add src/loopmetry/cli.py tests/test_cli.py
git commit -m "feat: add consented loopmetry history discover/preview/import"
```

---

### Task 6: Documentation, decision-log follow-through, and end-to-end check

**Files:**
- Modify: `docs/hook-capture.md` (replace "planned" backfill wording with the shipped commands)
- Modify: `docs/roadmap.md` (mark slice 2 implemented)
- Modify: `docs/submission-workflow.md` (participant flow: note that backfilled events in `.loopmetry/events/` are picked up by `loopmetry run` discovery exactly like hook events)
- Modify: `AGENTS.md` routing row (add `src/loopmetry/adapters/claude_code_history.py`, `tests/test_claude_code_history.py`)

- [ ] **Step 1: Update the four documents**

In `docs/hook-capture.md` "Relationship to historical backfill": change "A consented historical-backfill adapter (planned) can recover…" to present tense and document the three commands with one example:

```bash
loopmetry history preview --source claude-code
loopmetry history import --source claude-code --since 2026-08-01
```

noting: bounded to this project's sessions, mtime-window `--since`, interactive confirmation or explicit `--yes`, output under `.loopmetry/events/`, incremental via `.loopmetry/checkpoints/`. In `docs/roadmap.md`, prefix slice 2 with "*(implemented)*". In `AGENTS.md`, extend the adapter routing row. In `docs/submission-workflow.md`, add one sentence where input discovery is described.

- [ ] **Step 2: End-to-end verification**

```bash
uv run python -m unittest discover -s tests -v
uvx --from ruff==0.12.12 ruff check .
uv run loopmetry run --input examples/demo_project.jsonl --assignment-id demo --submitter-id local
uv build
```

Then a manual smoke test on this very repository (it has real local Claude Code history):

```bash
uv run loopmetry history preview --source claude-code --root .
```

Expected: a candidate list with no import performed. Do not commit any generated `.loopmetry/` output.

- [ ] **Step 3: Commit**

```bash
git add docs/hook-capture.md docs/roadmap.md docs/submission-workflow.md AGENTS.md
git commit -m "docs: document consented Claude Code history backfill"
```

---

## Out of scope

- Codex/Cursor history (slice 5; the Codex facts already gathered live in the local, uncommitted notes at `.loopmetry/notes/history-backfill-research.md`).
- Hybrid `--source auto`, hook/backfill merge, and `adapter_conflict` surfacing (slice 4).
- Subagent transcript import, `sessions-index.json`-based attribution, and dead-cwd recovery (worktrees/renames) — future enhancements; discovery diagnostics already make the gap visible.
- `loopmetry integrate` (slice 3).
