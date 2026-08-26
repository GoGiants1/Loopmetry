# Slice 5 — Codex parity

**Status:** Draft, approved for planning
**Roadmap:** `docs/roadmap.md` milestone 2, slice 5
**Related decisions:** `docs/decision-log.md` D-011, D-012, D-013, D-014
**Research input:** `.loopmetry/notes/history-backfill-research.md` (uncommitted, local Paxel analysis — Codex facts section); rollout wire schema additionally verified directly against `openai/codex` source (`codex-rs/protocol/src/protocol.rs`, `codex-rs/protocol/src/models.rs`, `codex-rs/rollout/src/list.rs`, `codex-rs/rollout/src/policy.rs`, `codex-rs/core/src/tools/hook_names.rs`; repo HEAD as of 2026-08-25) via `gh api`/`gh search code` — cited inline in §1 below.

## Context

Milestone 2 gives Claude Code and Codex parity as first-class source adapters behind the shared `SourceAdapter` contract (D-011). Slices 1–3 implemented the shared foundation and both Claude Code paths (historical backfill in slice 2, hook integration in slice 3); both are merged to `main`. Codex already has hook *capture* (`loopmetry capture-hook --source codex`, `src/loopmetry/hook_capture.py`) and its `.codex/config.toml` documentation is written but manual (`docs/hook-capture.md`). This slice closes the remaining Codex gaps: historical backfill parsing, a hook-config installer, and the tests that keep both sources verifiably comparable.

This slice does not depend on slice 4 (hybrid `--source auto` mode) and is being built in an isolated worktree while slice 4 proceeds independently elsewhere. The two will be connected in a later integration pass.

## Scope

In scope:
1. Codex historical backfill adapter (`CodexHistoryAdapter`).
2. Codex hook-config installer (`loopmetry integrate codex`).
3. Coverage-comparability tests and doc updates (no new runtime "matrix" artifact).
4. Cross-source (Claude Code + Codex) merge tests using existing merge machinery.

Out of scope (explicitly deferred, not solved here):
- Any `--source auto` merge orchestration, CLI, or run-manifest changes (slice 4).
- Participant report source-coverage sections (slice 6).
- Any change to `adapters/base.py`, `event_merge.py`, or `adapters/checkpoints.py` — both are already source-agnostic and need no modification.

## 1. Codex historical adapter

**File:** `src/loopmetry/adapters/codex_history.py`
**Version constant:** `CODEX_HISTORY_ADAPTER_VERSION = "1.0.0"`

### Verified on-disk format (confirmed against `openai/codex` source, not just the earlier Paxel-derived research notes)

The research notes' Codex facts were directional; the exact wire schema below was confirmed by reading `openai/codex`'s own Rust source (`codex-rs/protocol/src/protocol.rs`, `codex-rs/protocol/src/models.rs`, `codex-rs/rollout/src/list.rs`, `codex-rs/rollout/src/policy.rs`, `codex-rs/core/src/tools/hook_names.rs` — repo HEAD as of 2026-08-25; re-check against current `main` if drift is suspected, since `docs/hook-capture.md` already documents this format as vendor-unstable):

- **Path**: `~/.codex/sessions/YYYY/MM/DD/rollout-YYYY-MM-DDThh-mm-ss-<uuid>.jsonl` (fixed 3-level date nesting, not the unbounded depth the research notes guessed — but discovery should still glob recursively (`sessions/**/*.jsonl`) rather than hardcode the 3-level structure, since that's an implementation detail that could change).
- **Every line** is `{"timestamp": "...", "ordinal": <int, optional>, "type": "<kind>", "payload": {...}}` — `type` is the envelope kind (`session_meta`, `response_item`, `event_msg`, `turn_context`, `compacted`, `world_state`, `security_risk_score`, `inter_agent_communication[_metadata]`, `realtime_item`). Only `session_meta` and `response_item` carry evidence this adapter cares about; every other kind is a skip-with-count diagnostic (`skipped_record_type`), exactly like the Claude Code adapter's handling of non-`user`/`assistant` records.
- **`session_meta` payload** (flattened `SessionMeta` + optional `git`): `{"session_id", "id", "timestamp", "cwd", "originator", "cli_version", "source"?, "model_provider"?, "git"?: {"commit_hash"?, "branch"?, "repository_url"?}}`. `git.repository_url` is flat (not nested further) — the research notes' "may appear nested vs flat" caveat does not apply to this field specifically; it only ever appears at `payload.git.repository_url`.
- **`response_item` payload** is itself tagged (`{"type": "message"|"local_shell_call"|"function_call"|"function_call_output"|"reasoning"|"agent_message"|..., ...}`). Variants this adapter parses:
  - `message`: `{"role": "user"|"assistant"|"developer"|"system", "content": [{"type": "input_text"|"output_text"|"input_image"|"input_audio", "text"?: "..."}]}`.
  - `local_shell_call`: `{"call_id", "status": "completed"|"in_progress"|"incomplete", "action": {"type": "exec", "command": ["bash", "-lc", "..."] or ["apply_patch", "*** Begin Patch..."], "timeout_ms"?, "working_directory"?, "env"?, "user"?}}`. `action.command[0] == "apply_patch"` is how a file-change call is distinguished from a shell call at this layer (confirmed via `codex-rs/core/src/tools/hook_names.rs`, which documents `apply_patch` and `Bash` as Codex's two canonical hook-facing tool names, and `codex-rs/models-manager/prompt.md`, which documents `apply_patch` being invoked as `{"command": ["apply_patch", "<patch text>"]}` — the same `command` array shape as a shell call).
  - `function_call`: `{"call_id", "name", "arguments": "<JSON-encoded string>"}` — an alternate tool-calling path to `local_shell_call`; only `arguments` needs parsing (as JSON) if a `command`-shaped field is present inside it.
  - `function_call_output`: `{"call_id", "output": <plain string, OR an array of structured content items — never an object with an explicit boolean error field>}`.
- **No exit-code or success/failure signal is persisted anywhere in this format.** `FunctionCallOutputPayload`'s wire form is confirmed (via its custom `Serialize` impl) to contain only the output body text/content-items — the in-process `success: Option<bool>` field is never serialized. `LocalShellStatus` (`Completed`/`InProgress`/`Incomplete`) reflects whether the call *finished running*, not whether it exited zero. **This is a genuine, permanent gap in the historical-backfill source, not a parsing shortcut**: unlike the live hook path (which gets a real exit status in the hook JSON payload), Codex's rollout file never records one. Per invariant 4, this adapter must never infer success from the mere presence of an output — every Codex-sourced `command` event's `status` is `"unknown"`, and coverage for `verifications`/`errors` is `partial` for this source, permanently (not just when something goes wrong), with a `command_status_unavailable` diagnostic. Document this plainly in `docs/hook-capture.md`'s coverage table as a Codex-specific, historical-backfill-specific limitation — it does not apply to Codex hook capture.

### Discovery

- Root: recursive glob `~/.codex/sessions/**/*.jsonl` (see confirmed path shape above; glob recursively rather than assuming the exact 3-level nesting).
- Home override: `LOOPMETRY_CODEX_HOME` env var (parallel to `LOOPMETRY_CLAUDE_HOME`), read in `cli.py` and passed to the adapter constructor, same pattern as `ClaudeCodeHistoryAdapter(claude_home=...)`.
- Bounded by `DiscoveryContext.since`/`until`/`project_root`; never reads outside those bounds. A session file is appended to for as long as it's active, so mtime cannot bound the event timestamps inside it — same reasoning as both existing adapters; the window is enforced per-event in `import_candidates`, not in `discover`.

### Attribution (session → project)

`SessionMeta` carries `cwd` directly (confirmed field, see above) — Codex needs no repository-remote-based attribution at all; it can use the same cwd-based scoping as the Claude Code adapter, which is simpler and strictly more precise than remote matching:

- Read the `session_meta`-type record (scan for `type == "session_meta"`, don't assume it's line 1, though it always is in practice — cheap defensiveness, same posture as `_session_cwd`'s `queue-operation`-skipping scan) and take `payload.cwd`.
- Reuse `_cwd_in_scope`-equivalent logic (candidate's resolved `cwd` == project root or project root in its parents) — same helper shape as `claude_code_history.py`'s `_cwd_in_scope`, either imported and shared or duplicated verbatim (duplicate is fine; it's a 6-line pure function, not worth cross-module coupling for).
- `payload.git.repository_url`, when present, is recorded as an extra diagnostic aid only (e.g. surfaced in a future coverage/report context) — it is not required for attribution and its absence is never a reason to exclude a session.
- A session whose `session_meta` is missing, unparsable, or whose `cwd` doesn't match is **excluded**, not widened into scope (D-012 contrast principle) — same as Claude Code. Every excluded session is still visible via a `Diagnostic` (kind `unattributed_session`), never a silent drop.

### Parsing

- One `_SessionParser`-equivalent per candidate file, same event categories (`EVIDENCE_CATEGORIES` from `adapters/base.py`) and `minimize.py` helpers as Claude Code (hashing, `safe_relative_path`, `command_signature`) — no new minimization rules.
- **Pending/pairing is generalized to "keyed by `call_id`, resolved by any later record carrying the same `call_id`,"** rather than assuming Claude Code's exact `tool_use`/`tool_result` shape — Codex has two structurally different call shapes that both need this (see verified schema above): `function_call` → `function_call_output` (always two records), and `local_shell_call` (which may appear once already in a terminal `status`, or twice — first `in_progress`, later `completed`/`incomplete` — the format doesn't guarantee which, so the parser must handle both). The D-013 contract applies unchanged: `Checkpoint.positions[candidate_id]["pending"]` carries unresolved `call_id` state across imports; unresolved entries finalize to `status="unknown"` only when an import observes zero file growth since the position where the entry was left; `unresolved_tool_call`/`stalled_tool_call` diagnostics degrade `commands` coverage to `partial`.
- **Every resolved command's `status` is `"unknown"`** (see the verified-format note above on the permanent absence of an exit-code/success signal in this source) — this is not the same "unknown" as a stalled/never-resolved call; both use the same schema value because `EventType.COMMAND`'s `data.status` field has no third state, but the `command_status_unavailable` diagnostic (emitted once per import run when any command event was produced, not once per command) disambiguates "resolved but status unknowable" from "never resolved" for anyone reading diagnostics rather than just coverage.
- `action.command[0] == "apply_patch"` → `EventType.FILE_CHANGE` (best-effort path extraction from the patch text's `*** Update File:`/`*** Add File:` header — if no path header is extractable, count `unextractable_path` and drop, same fail-safe posture as Claude Code's `safe_relative_path is None` branch). Any other `action.command` → `EventType.COMMAND` via `command_signature(" ".join(command))`.
- `message` records: `role == "user"` → `EventType.HUMAN_INTERVENTION` (hash + length only, never raw text, same as Claude Code's prompt event) from `input_text` content items; `role in {"assistant", "developer", "system"}` → not imported as evidence (no Claude-Code-style plan/read/write signal exists in a bare `message` item — those come from `local_shell_call`/`function_call` items instead).
- Unknown/unexpected envelope kinds (`event_msg`, `turn_context`, `compacted`, `world_state`, etc.) and unknown `response_item` payload types (`reasoning`, `agent_message`, `tool_search_call`, ...) are skip-with-count diagnostics; one malformed or unrecognized line must never abort the rest of a session's parse.
- Content caps mirror the Claude Code adapter's existing `_MAX_RECORD_BYTES` (reuse the same constant/behavior rather than inventing a second cap).

### Checkpointing

- Reuses `adapters/checkpoints.py` unmodified — `checkpoint_path(root, "codex")`, `load_checkpoint`, `save_checkpoint`, `atomic_write_bytes`. No code changes needed there; confirmed by reading the current implementation (keyed generically by `source: str`).

### CLI wiring

- `src/loopmetry/cli.py`: add `"codex": CodexHistoryAdapter` to `_HISTORY_ADAPTERS`; `history discover|preview|import --source codex` gets the existing generic `_run_history` code path for free (it already dispatches purely off `_HISTORY_ADAPTERS[args.source]`).
- Default output path when `--source codex`: `<root>/.loopmetry/events/codex-history.jsonl` (parallel to the existing `claude-code-history.jsonl` default — `_run_history` needs a small per-source default-path branch, currently hardcoded to the Claude Code filename).

## 2. Codex hook-config installer

**Files:** `src/loopmetry/hook_integration_codex.py` (pure logic, no I/O — mirrors `hook_integration.py`'s split), `cli.py` wiring in `_run_integrate`.

### Design

No stdlib TOML writer exists (`tomllib` is read-only). Approach:

- **Read/validate/detect** with `tomllib.load`: parse the existing `.codex/config.toml` (if present) to confirm it's valid TOML, that top-level `hooks` (if present) is a table, and that each targeted event's value (if present) is an array of tables. Any violation is a hard error on `--preview`, `--apply`, and `--remove` alike — same fail-closed rule as the JSON installer (D-014), never a partial or silent overwrite.
- **Ownership detection**: a `[[hooks.<Event>]]` entry is installer-owned only if it exactly matches what this installer would generate for that event — one nested `[[hooks.<Event>.hooks]]` table with exactly `type = "command"`, `command = "loopmetry"`, `args = [...]` matching `_BASE_ARGS` (`["capture-hook", "--source", "codex"]`) plus an optional trailing `--project-id <value>`, and `timeout` set to the installer's fixed value. An entry scoped by any additional key, or with extra/missing fields, is never touched by `--remove` and is never treated as satisfying integration — same principle as `hook_integration.py`'s `_owned_block`/`_owned_args`, adapted to TOML's table-array shape instead of JSON's block shape.
- **Write via text templating, not serialization**: because the file may contain comments, formatting, and unrelated tables that a round-tripped `tomllib`-parsed-then-re-emitted document would not preserve byte-for-byte, the installer never re-serializes the whole file. It locates the owned blocks it detected (by their source-text span) and replaces exactly those spans with a freshly generated block; new events not yet present get their block appended once, using the same template. Every other byte of the file is untouched.
- **Force/backup policy**: identical to D-014 — no `--force` required when the file doesn't exist or the computed result is byte-identical (true no-op re-apply); required whenever an existing file's content would actually change; single overwritten `.bak` written immediately before any modifying write.
- `--remove` is scoped only to the five events this installer manages (`UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `TaskCompleted`, `SessionEnd` — reuse `INTEGRATION_HOOK_EVENTS` from `hook_integration.py` rather than redefining it), never touching arbitrary hooks already in the file.
- **Command shape (resolved — was an open question):** unlike the Claude Code JSON installer, Codex hook entries have no `args` array — `command` is a single shell-parsed string (confirmed against `docs/hook-capture.md`'s existing Codex example and external Codex hooks documentation). Because the shell, not `execve`, splits this string, `--project-id` must be embedded via `shlex.quote()` rather than passed as a separate array element; an unquoted project ID containing spaces or shell metacharacters would otherwise be word-split or interpreted by the shell. The installer generates `command = "loopmetry capture-hook --source codex"` (plus `` --project-id {shlex.quote(project_id)}`` when set) and `timeout = 3`, matching the doc's existing example. Ownership detection re-parses a candidate entry's `command` string with `shlex.split()` and checks it against the same prefix/optional-`--project-id` structure `_owned_args` already uses for the JSON installer, rather than comparing raw strings byte-for-byte (so quoting-style differences that still `shlex.split()` to the same tokens are still recognized as ours).

### CLI wiring

- `integrate.add_argument("source", choices=(...))` → add `"codex"`.
- `_run_integrate` dispatches to `hook_integration_codex` when `args.source == "codex"`, reusing the same diff-preview (`difflib`), backup, and `atomic_write_bytes` flow `cli.py` already owns for the JSON path — only the block-generation/ownership-detection logic differs per source.

### Decision log

Once the TOML merge design is implemented as described, add a new dated entry (D-016 after integration with slice 4's D-015) documenting the text-templated-write-over-parsed-detection approach, its rationale (no stdlib TOML writer, must preserve comments/formatting), and its consequences — following the same structure as D-014.

## 3. Coverage comparability (no new runtime artifact)

- Add a test (in `tests/test_codex_history.py` or `tests/test_adapters.py`) asserting `ClaudeCodeHistoryAdapter().capabilities()` and `CodexHistoryAdapter().capabilities()` report the same `evidence_categories` shape (subset of the shared `EVIDENCE_CATEGORIES`) and that both adapters' `CoverageReport.categories` only ever use `Coverage` enum values — a structural regression guard, not a behavioral claim that coverage is equal.
- Update `docs/hook-capture.md`'s existing Claude Code/Codex coverage table to reflect what the new adapter actually supports (move relevant rows from "planned" to "supported", and add the `unattributed_session` limitation noted above as an explicit row/footnote).
- Update `docs/roadmap.md` slice 5 line to "implemented" once merged, following the same pattern as slices 1–3.

## 4. Cross-source merge tests

- No new merge code. Add tests (in `tests/test_adapters.py`, alongside existing dual-source tests) that build small synthetic event sets mixing `source="claude-code"` (hook + history-backfill) and `source="codex"` (hook + history-backfill) capture modes, and assert:
  - non-conflicting overlapping `event_id`s merge via `event_merge.merge_events` with accumulated provenance and no data loss, regardless of which two sources/capture-modes overlap;
  - genuinely conflicting same-`event_id` events across sources still raise `EventConflictError`, exactly as same-source conflicts do today;
  - `EventStore.add_events` (or the equivalent ingest path) produces the same result for a cross-source batch as for a single-source batch with the same logical content.

## Testing plan

- `tests/test_codex_history.py`: discovery bounds, attribution (matching/non-matching/missing `cwd`), `local_shell_call`/`function_call`+`function_call_output` parsing, `apply_patch` path extraction, call-id pairing/checkpoint resume (mirroring the Claude Code adapter's existing checkpoint-resume tests), unknown-record diagnostics, content-cap behavior, universal `status="unknown"` on emitted commands.
- `tests/test_hook_integration_codex.py`: block generation, ownership detection (exact match vs. scoped/extra-field variants never matched), force/backup policy, invalid-existing-file hard errors, remove-scoping, no-op re-apply.
- `tests/test_adapters.py`: capability/coverage structural comparability; cross-source merge behavior.
- `tests/test_cli.py`: `history --source codex` end-to-end (discover/preview/import against fixture files), `integrate codex --preview|--apply|--remove` end-to-end.
- Full suite: `uv run python -m unittest discover -s tests -v`; lint: `uvx --from ruff==0.12.12 ruff check .`.

## Documentation and decision-log updates required

- `docs/hook-capture.md`: coverage table, mark Codex history backfill and hook integration as supported, document the `unattributed_session` limitation.
- `docs/roadmap.md`: mark slice 5 implemented.
- `docs/decision-log.md`: new entry for the TOML text-templated merge design (see §2).
- `AGENTS.md`: add a routing row (or extend the existing "Source adapters and historical backfill" / "Hook integration installer" rows) pointing to `codex_history.py` and `hook_integration_codex.py`.

## Open questions

None outstanding. The one open question in the original draft (Codex's TOML hook-handler shape) is resolved above in §2: single shell-parsed `command` string, no `args` array, `shlex.quote()` for embedded values.
