# Slice 4: `loopmetry run --source auto` (hybrid auto mode)

## Context

Milestone 2 slices 1–3 are merged to `main` (`287e0f4`): the shared `SourceAdapter`
contract (D-011), the Claude Code historical-backfill adapter, and the
`loopmetry integrate claude-code` hook installer (D-014). `docs/roadmap.md` scopes
slice 4 as:

> **Hybrid auto mode.** `loopmetry run --source auto`: merge hook, explicit, and
> consented history evidence; deduplicate with provenance preserved; surface
> `adapter_conflict` diagnostics and a coverage summary. Interactive terminals
> preview history candidates and ask before import; non-interactive runs use hook
> and explicit input only unless `--include-history` is passed. Default backfill
> window is the assignment's configured start/end (decision D-012), not an
> unbounded or fixed-lookback default.

Today, `loopmetry run` (`src/loopmetry/workflow.py`, `src/loopmetry/cli.py`)
discovers `.loopmetry/hooks/*.jsonl` and `.loopmetry/events/*.jsonl` (or takes
explicit `--input`, mutually exclusive with discovery) and hard-fails
(`EventConflictError` → `InputError`) if two same-`event_id` events disagree on
content. `loopmetry history import` (already merged) has its own consent flow,
discovery/preview/import call into `ClaudeCodeHistoryAdapter`, checkpoint
load/save, and merge-into-existing-output-file logic, all inlined in
`cli.py::_run_history`. This slice adds a new opt-in `run` mode that actively
triggers a consented history import as part of the one-command flow and merges
its output with hook/explicit evidence, tolerating cross-source conflicts as
diagnostics instead of crashing.

**Scope note on D-012:** the assignment schema (`src/loopmetry/admin_storage.py`)
has no `starts_at`/`ends_at` field, and D-012 itself only commits to the
assignment-window default "once the administrator schema carries them." This
slice does **not** add that schema. It implements `--since`/`--until` as explicit
per-invocation bounds and records in the decision log that the assignment-window
default remains blocked on separate assignment-schema work.

## Decisions

1. **`--source` is a new, opt-in argument on `run`.** `choices=("auto",)`. Omitting
   it is byte-for-byte today's behavior — no new prompts, no active history scan,
   no manifest/output changes. This keeps every existing `run` test and documented
   default unchanged.
2. **History consent is factored out of `_run_history`, not duplicated.** The
   discover→preview→consent-prompt→import→checkpoint-save→merge-into-file body
   currently inlined in `cli.py::_run_history`'s `import` branch becomes a shared
   helper used by both `history import` and `run --source auto`.
3. **Non-interactive consent semantics differ from `history import` on purpose.**
   `history import --yes` is required non-interactively or it's a hard error
   (existing behavior, unchanged). `run --source auto` without `--include-history`
   in a non-interactive shell is **not** an error — it silently skips history and
   proceeds with hook + explicit evidence only, per the roadmap's literal wording.
   `run` is the one-command path; it must not abort a routine analysis run over an
   omitted optional flag.
4. **Cross-source conflicts become diagnostics, only on this new path.** A new
   `merge_events_tolerant` (in `event_merge.py`) and `load_event_files_with_diagnostics`
   (in `workflow.py`) keep the existing `Event` on conflict and report it as an
   aggregated `Diagnostic(kind="adapter_conflict")` instead of raising
   `EventConflictError`. `load_event_files` (used by default `run`, `history
   import`, `ingest`) is untouched — a conflict there still hard-fails, since those
   paths merge same-adapter files where a conflict is more likely corruption than a
   legitimate cross-source disagreement. `run_participant_workflow` gains a
   `strict: bool = True` parameter; `--source auto` passes `strict=False`.
5. **Diagnostics surface at the CLI/manifest level only, not in the report.**
   `run --source auto` prints a diagnostics/coverage summary line (matching
   `history import`'s existing style) and `manifest.json` gains an optional
   `source_coverage` block only when `--source auto` was used. `report.json`,
   `report.html`, and `ProjectReport` are untouched — roadmap slice 6
   ("Participant report source coverage") owns adding real report sections later.
   No per-metric confidence score is touched by this slice; "lower confidence" is
   satisfied by making the conflict visible to a human reader, not by mutating
   `metrics_*.py` math.
6. **Auto mode's source-file gathering is additive, not either/or.** Today,
   `_participant_source_files` returns explicit `--input` files *instead of*
   discovered files when `--input` is given. In `--source auto`, source files are
   the union of `discover_event_files(root)` and explicit `--input` (deduplicated
   by resolved path) — this only changes behavior when `--source auto` is passed.

## Design

### CLI surface (`cli.py`)

```
loopmetry run --source auto [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--include-history] \
  [existing --input/--root/--project-id/--assignment-id/--submitter-id/--server/... flags]
```

- `run.add_argument("--source", choices=("auto",))` — absent by default.
- `run.add_argument("--since", ...)` / `run.add_argument("--until", ...)` — reuse
  `_parse_since`; add a symmetric `_parse_until` (same parsing, just a different
  field name/error message). Both `None` unless passed. Ignored unless
  `--source auto`.
- `run.add_argument("--include-history", action="store_true")` — non-interactive
  consent for the history scan. Ignored unless `--source auto`.
- `--root` is reused (already exists on `run`) as the bound for history discovery,
  matching `history import`'s `--root`.

### Shared history-import helper (`cli.py`)

Extract from `_run_history`'s `import` branch into:

```python
def _consented_history_import(
    adapter: SourceAdapter,
    context: DiscoveryContext,
    root: Path,
    *,
    interactive: bool,
    consent_given: bool,  # True if --yes (history import) or --include-history (run auto)
    output_path: Path | None = None,
) -> AdapterRun | None:
    """Returns None if the user declines or non-interactive consent is absent.

    Owns: the two-step interactive prompt (scan? then proceed with N sessions?),
    discover/preview/import_candidates, checkpoint load/save, and merging the new
    run's events into the existing history output file (today's dict-by-id +
    merge_events + EventConflictError->InputError logic, unchanged — a conflict
    *within* the history adapter's own output file is still a hard error; only
    conflicts *across* hook/explicit/history sources in step "workflow merge" below
    are tolerant).
    """
```

`history import`'s call site passes `consent_given=args.yes`, preserving its
existing hard-fail-if-non-interactic-and-not-yes check (that check stays where it
is, before calling this helper, since it's a hard error there and not here).
`run --source auto`'s call site passes `consent_given=args.include_history` and,
when `interactive`, lets the helper run the identical prompt flow; when
non-interactive and `consent_given` is `False`, the call is skipped entirely
(printing nothing — same as if `--source auto` had never triggered a scan).

### Tolerant merge (`event_merge.py`, `workflow.py`)

```python
# event_merge.py
def merge_events_tolerant(existing: Event, incoming: Event) -> tuple[Event, bool]:
    """Like merge_events, but returns (existing, True) instead of raising on conflict."""
    if events_conflict(existing, incoming):
        return existing, True
    return merge_events(existing, incoming), False
```

```python
# workflow.py
def load_event_files_with_diagnostics(
    paths: Iterable[str | Path],
) -> tuple[list[Event], tuple[Diagnostic, ...]]:
    """Like load_event_files, but conflicting duplicate event_ids become an
    aggregated adapter_conflict Diagnostic instead of raising. Each conflicting
    event_id is also printed to stderr at merge time (Diagnostic has no id-list
    field, so this is how traceability to specific IDs is preserved without a
    schema change)."""
```

`run_participant_workflow` gains `strict: bool = True`. When `False`, it calls
`load_event_files_with_diagnostics` instead of `load_event_files`, and attaches
the diagnostics to a new `RunArtifacts.source_diagnostics: tuple[Diagnostic, ...] = ()`
field.

### `_run_run` orchestration (`cli.py`)

When `args.source == "auto"`:

1. Build `DiscoveryContext(project_root=root, since=_parse_since(args.since), until=_parse_until(args.until), interactive=sys.stdin.isatty())`.
2. Call `_consented_history_import(...)`. If it returns an `AdapterRun`, print its
   diagnostic/coverage summary (same style as `history import`) before continuing.
   If it returns `None`, print nothing extra — proceed as if auto mode found no
   history to add.
3. Compute source files as the union of `discover_event_files(root)` and
   `args.input` (deduplicated), instead of calling `_participant_source_files`.
4. Call `run_participant_workflow(source_files, ..., strict=False)`.
5. After the call, if `artifacts.source_diagnostics` is non-empty, print a summary
   line (e.g. `source diagnostics: adapter_conflict=2`).

When `args.source` is absent: exactly today's code path (`_participant_source_files`,
`strict=True` default) — no behavioral change.

### Manifest (`workflow.py::_write_run_manifest`)

Add an optional `source_coverage` key, populated only by the auto-mode call site:

```json
{
  "source_coverage": {
    "mode": "auto",
    "history_included": true,
    "diagnostics": [{"kind": "adapter_conflict", "summary": "...", "count": 2}]
  }
}
```

Default `run` (no `--source`) omits this key entirely (`None`), matching today's
manifest shape exactly.

## Testing

- `tests/test_event_merge.py` (new or extended): `merge_events_tolerant` returns
  `(existing, False)` for a true no-op or additive-provenance merge, and
  `(existing, True)` for a genuine content conflict, never raising.
- `tests/test_workflow.py`: `load_event_files_with_diagnostics` — no conflicts
  (empty diagnostics, same result as `load_event_files`), one conflicting pair
  (existing kept, one aggregated `adapter_conflict` diagnostic with count 1), and
  multiple conflicts aggregating into one diagnostic with the right count.
  `run_participant_workflow(..., strict=False)` populates
  `RunArtifacts.source_diagnostics`; `strict=True` (default) behavior and every
  existing test are unchanged.
- `tests/test_cli.py`: new `run --source auto` subprocess tests —
  non-interactive without `--include-history` (history skipped, no prompt, no
  error, normal run output); non-interactive with `--include-history` and a fake
  Claude Code history fixture under a scratch `claude_home` (history imported,
  merged into source files, run succeeds); a deliberately conflicting fixture
  (hook event and history event share an `event_id` with different content) run
  succeeds with `source diagnostics: adapter_conflict=1` printed and present in
  `manifest.json`, rather than failing; `--since`/`--until` narrow the
  `DiscoveryContext` passed to the adapter (assert via a stub/fake adapter or by
  checking which fixture sessions get imported); default `run` (no `--source`)
  behaves exactly as before — explicit regression check, not just relying on
  existing tests still passing.
- Full suite + `ruff check .` must stay green; no existing test's expected output
  (manifest shape, stdout text) may change when `--source` is omitted.

## Decision log

Add **D-015**: "Hybrid auto-merge tolerates cross-source conflicts as diagnostics,
scoped to `run --source auto` only; the D-012 assignment-window default remains
unimplemented pending assignment-schema work; non-interactive `--include-history`
absence is a skip, not an error." Status Accepted, with Context/Decision/
Consequences/Related following the existing D-011–D-014 format, linking
`docs/roadmap.md` milestone 2 slice 4, D-011, D-012, and the new code paths in
`event_merge.py`, `workflow.py`, `cli.py`.

## Docs

- `docs/hook-capture.md`: document `run --source auto`'s consent semantics
  (interactive prompts, `--include-history` non-interactive skip-not-error,
  `--since`/`--until`), the `adapter_conflict` diagnostic, and the
  `manifest.json` `source_coverage` block.
- `docs/roadmap.md`: mark slice 4 `*(implemented)*`.
- `AGENTS.md`: routing table row for hybrid auto mode pointing at
  `docs/hook-capture.md`, `docs/decision-log.md` D-015,
  `src/loopmetry/cli.py`/`workflow.py`/`event_merge.py`, and the corresponding
  test files.
