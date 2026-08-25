# Slice 5 — Codex parity

**Status:** Draft, approved for planning
**Roadmap:** `docs/roadmap.md` milestone 2, slice 5
**Related decisions:** `docs/decision-log.md` D-011, D-012, D-013, D-014
**Research input:** `.loopmetry/notes/history-backfill-research.md` (uncommitted, local Paxel analysis — Codex facts section)

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

### Discovery

- Root: `~/.codex/sessions/**/*.jsonl` (recursive glob; nesting depth is ~6 per the research notes — do not assume a fixed depth, glob recursively).
- Home override: `LOOPMETRY_CODEX_HOME` env var (parallel to `LOOPMETRY_CLAUDE_HOME`), read in `cli.py` and passed to the adapter constructor, same pattern as `ClaudeCodeHistoryAdapter(claude_home=...)`.
- Bounded by `DiscoveryContext.since`/`until`/`project_root`; never reads outside those bounds. Time-window filtering falls back to file mtime when a record's own timestamp is unavailable (research notes: "mtime-based time-window filtering when record timestamps are unavailable").

### Attribution (session → project)

- First-line `session_meta` record carries `git.repository_url`. Probe both nested (`.payload.git.repository_url`) and flat (`.git.repository_url`) shapes — never assume one; this is a documented Codex version-drift fact, not a hypothetical.
- Normalize both the recorded remote and the current project's remote before comparing: strip scheme, rewrite `git@host:` → `host/`, strip trailing `.git` and `/`, strip `user@`. Two remotes that normalize equal attribute the session to this project.
- A session whose `session_meta` is missing, unparsable, or whose normalized remote doesn't match is **excluded**, not widened into scope (D-012 contrast principle — Loopmetry does not follow Paxel's broader-attribution fallback chain for this adapter version). This is a deliberate simplification versus Claude Code's cwd-based attribution, which has no repository-remote equivalent available; record it as a known adapter limitation in `docs/hook-capture.md`, not as a silent gap.
- Every excluded session is still visible via a `Diagnostic` (kind `unattributed_session`), never a silent drop.

### Parsing

- One `_SessionParser`-equivalent per candidate file, same event categories (`EVIDENCE_CATEGORIES` from `adapters/base.py`) and `minimize.py` helpers as Claude Code (hashing, `safe_relative_path`, `command_signature`) — no new minimization rules.
- Tool-call pairing reuses the D-013 contract exactly: `Checkpoint.positions[candidate_id]["pending"]` carries unresolved tool-call state across imports; unresolved entries finalize to `status="unknown"` only when an import observes zero file growth since the position where the entry was left; `unresolved_tool_call` / `stalled_tool_call` diagnostics degrade `commands` coverage to `partial`, exactly as for Claude Code.
- Unknown/unexpected record kinds (Codex housekeeping records analogous to Claude Code's `queue-operation`, `file-history-snapshot`) are skip-with-count diagnostics; one malformed line must never abort the rest of a session's parse.
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
- Generated command form matches the documented example in `docs/hook-capture.md`'s Codex section: `command = "loopmetry"`, args-equivalent via Codex's TOML shape (Codex hooks use `command`/`args`-style keys per the existing doc — confirm exact key names against `docs/hook-capture.md`'s current example when implementing; do not invent new key names).

### CLI wiring

- `integrate.add_argument("source", choices=(...))` → add `"codex"`.
- `_run_integrate` dispatches to `hook_integration_codex` when `args.source == "codex"`, reusing the same diff-preview (`difflib`), backup, and `atomic_write_bytes` flow `cli.py` already owns for the JSON path — only the block-generation/ownership-detection logic differs per source.

### Decision log

Once the TOML merge design is implemented as described, add a new dated entry (next available ID, e.g. D-015) documenting the text-templated-write-over-parsed-detection approach, its rationale (no stdlib TOML writer, must preserve comments/formatting), and its consequences — following the same structure as D-014.

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

- `tests/test_codex_history.py`: discovery bounds, attribution (matching/non-matching/missing remote), dual payload-shape parsing, tool-call pairing/checkpoint resume (mirroring `tests/test_hook_capture.py`'s or the Claude Code adapter's existing checkpoint-resume tests), unknown-record diagnostics, content-cap behavior.
- `tests/test_hook_integration_codex.py`: block generation, ownership detection (exact match vs. scoped/extra-field variants never matched), force/backup policy, invalid-existing-file hard errors, remove-scoping, no-op re-apply.
- `tests/test_adapters.py`: capability/coverage structural comparability; cross-source merge behavior.
- `tests/test_cli.py`: `history --source codex` end-to-end (discover/preview/import against fixture files), `integrate codex --preview|--apply|--remove` end-to-end.
- Full suite: `uv run python -m unittest discover -s tests -v`; lint: `uvx --from ruff==0.12.12 ruff check .`.

## Documentation and decision-log updates required

- `docs/hook-capture.md`: coverage table, mark Codex history backfill and hook integration as supported, document the `unattributed_session` limitation.
- `docs/roadmap.md`: mark slice 5 implemented.
- `docs/decision-log.md`: new entry for the TOML text-templated merge design (see §2).
- `AGENTS.md`: add a routing row (or extend the existing "Source adapters and historical backfill" / "Hook integration installer" rows) pointing to `codex_history.py` and `hook_integration_codex.py`.

## Open questions to resolve during implementation (not blocking plan-writing)

- Exact Codex TOML hook-handler key names (`command`/`args` vs. a single command string) must be re-confirmed against `docs/hook-capture.md`'s current documented example before the installer's template is written, since that doc was written before this adapter existed and its exact shape becomes a hard contract once an installer generates it.
