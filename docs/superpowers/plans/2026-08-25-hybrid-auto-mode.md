# Hybrid Auto Mode (`loopmetry run --source auto`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `loopmetry run --source auto` mode that actively triggers a
consented Claude Code history import as part of the one-command participant flow,
merges it with hook/explicit evidence, and tolerates cross-source content
conflicts as diagnostics instead of crashing — while leaving default `run`
behavior byte-for-byte unchanged.

**Architecture:** Two small, independently-testable additions below the existing
layers — a tolerant merge primitive in `event_merge.py`, and a diagnostics-aware
file loader in `workflow.py` used only when `run_participant_workflow(...,
strict=False)` — plus a `cli.py` refactor that factors the existing
`history import` consent/import body into a helper shared by both `history
import` and the new `run --source auto` path. No changes to `report.py`,
`evaluation.py`, `metrics_*.py`, or the canonical event schema.

**Tech Stack:** Python stdlib only (per `AGENTS.md`), existing `unittest`
test suite, `uv` for environment/lint/build.

## Global Constraints

- Runtime is standard-library-only; no new third-party dependencies (`AGENTS.md`).
- `uv run python -m unittest discover -s tests -v` and
  `uvx --from ruff==0.12.12 ruff check .` must stay green after every task.
- Default `loopmetry run` (no `--source` flag) must remain byte-for-byte
  identical in behavior, stdout, and `manifest.json` shape to what's on `main`
  today — every existing test for it must keep passing unmodified.
- Metric confidence math in `metrics_*.py`/`evaluation.py` is out of scope;
  conflicts surface only via CLI stdout and `manifest.json`, never by mutating a
  metric score (spec decision 5).
- `--since`/`--until` bound history discovery per-invocation only; no assignment
  schema changes (spec scope note on D-012).
- Every new/changed function needs a docstring or comment only where the *why*
  is non-obvious (e.g. why a conflict doesn't raise here) — no restating *what*
  the code does.

---

### Task 1: Tolerant event merge primitive

**Files:**
- Modify: `src/loopmetry/event_merge.py`
- Test: `tests/test_event_merge.py` (new file)

**Interfaces:**
- Produces: `merge_events_tolerant(existing: Event, incoming: Event) -> tuple[Event, bool]`
  — second element is `True` when the pair conflicted (content differs beyond
  provenance/schema_version). On conflict, returns `(existing, True)` unchanged
  — `incoming` is discarded, never partially merged. On no conflict, returns
  `(merge_events(existing, incoming), False)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_event_merge.py`:

```python
from __future__ import annotations

import unittest

from loopmetry.event_merge import merge_events_tolerant
from loopmetry.schema import Event, EventType, Actor


def _event(event_id: str = "evt-1", summary: str = "x", provenance: list | None = None) -> Event:
    return Event.from_mapping(
        {
            "schema_version": "0.2",
            "event_id": event_id,
            "project_id": "proj",
            "session_id": "sess",
            "timestamp": "2026-08-23T10:00:00Z",
            "type": EventType.NOTE.value,
            "actor": Actor.SYSTEM.value,
            "source": "claude-code",
            "data": {"summary": summary},
            "provenance": provenance or [],
        }
    )


class MergeEventsTolerantTests(unittest.TestCase):
    def test_no_conflict_merges_and_reports_false(self) -> None:
        existing = _event(
            provenance=[
                {"source": "claude-code", "capture_mode": "hook", "adapter_version": "1.0.0"}
            ]
        )
        incoming = _event(
            provenance=[
                {"source": "claude-code", "capture_mode": "history-backfill", "adapter_version": "1.0.0"}
            ]
        )
        merged, conflicted = merge_events_tolerant(existing, incoming)
        self.assertFalse(conflicted)
        self.assertEqual(len(merged.provenance), 2)

    def test_true_no_op_reports_false_and_returns_existing(self) -> None:
        existing = _event()
        incoming = _event()
        merged, conflicted = merge_events_tolerant(existing, incoming)
        self.assertFalse(conflicted)
        self.assertIs(merged, existing)

    def test_genuine_conflict_reports_true_and_keeps_existing_unchanged(self) -> None:
        existing = _event(summary="x")
        incoming = _event(summary="different")
        merged, conflicted = merge_events_tolerant(existing, incoming)
        self.assertTrue(conflicted)
        self.assertIs(merged, existing)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_event_merge -v`
Expected: `FAIL` / `ERROR` — `merge_events_tolerant` does not exist yet
(`ImportError`).

- [ ] **Step 3: Implement `merge_events_tolerant`**

In `src/loopmetry/event_merge.py`, add below `merge_events`:

```python
def merge_events_tolerant(existing: Event, incoming: Event) -> tuple[Event, bool]:
    """Like merge_events, but a genuine content conflict returns (existing, True)
    instead of raising EventConflictError.

    Used only by the hybrid auto-merge path (D-011): a disagreement between a
    hook observation and a history-backfill observation of the "same" event_id
    must stay visible as a diagnostic, not abort the whole run. merge_events
    itself is unchanged and keeps raising for same-adapter merges (history
    import, plain `run`, ingest), where a conflict more likely means corruption.
    """
    if events_conflict(existing, incoming):
        return existing, True
    return merge_events(existing, incoming), False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_event_merge -v`
Expected: `OK` (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/loopmetry/event_merge.py tests/test_event_merge.py
git commit -m "feat: add merge_events_tolerant for cross-source conflict handling"
```

---

### Task 2: Diagnostics-aware event-file loader

**Files:**
- Modify: `src/loopmetry/workflow.py`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `merge_events_tolerant` (Task 1); `Diagnostic` from
  `loopmetry.adapters.base` (fields: `kind: str`, `summary: str`, `count: int = 1`).
- Produces: `load_event_files_with_diagnostics(paths: Iterable[str | Path]) ->
  tuple[list[Event], tuple[Diagnostic, ...]]` — same dedup/sort behavior as
  `load_event_files`, except a conflicting pair keeps the first-seen event and
  is aggregated into one `Diagnostic(kind="adapter_conflict", count=N)` (never
  raises `InputError` for a conflict). Empty `paths` still raises `InputError`
  exactly like `load_event_files` does today.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workflow.py` (reuses the file's existing `_write_jsonl` and
`_base_event` helpers, already imported/defined at module scope):

```python
from loopmetry.adapters.base import Diagnostic
from loopmetry.workflow import load_event_files_with_diagnostics  # add to existing import block


class LoadEventFilesWithDiagnosticsTests(unittest.TestCase):
    def test_no_conflicts_matches_load_event_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            _write_jsonl(first, [_base_event(event_id="evt-1")])
            _write_jsonl(second, [_base_event(event_id="evt-2")])

            events, diagnostics = load_event_files_with_diagnostics([first, second])
            self.assertEqual(len(events), 2)
            self.assertEqual(diagnostics, ())

    def test_one_conflict_keeps_first_and_reports_one_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            _write_jsonl(first, [_base_event(data={"summary": "from-hook"})])
            _write_jsonl(second, [_base_event(data={"summary": "from-history"})])

            events, diagnostics = load_event_files_with_diagnostics([first, second])
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].data, {"summary": "from-hook"})
            self.assertEqual(len(diagnostics), 1)
            diagnostic = diagnostics[0]
            self.assertIsInstance(diagnostic, Diagnostic)
            self.assertEqual(diagnostic.kind, "adapter_conflict")
            self.assertEqual(diagnostic.count, 1)
            self.assertIn("evt-1", diagnostic.summary)

    def test_multiple_conflicts_aggregate_into_one_diagnostic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            _write_jsonl(
                first,
                [
                    _base_event(event_id="evt-1", data={"summary": "a1"}),
                    _base_event(event_id="evt-2", data={"summary": "a2"}),
                ],
            )
            _write_jsonl(
                second,
                [
                    _base_event(event_id="evt-1", data={"summary": "b1"}),
                    _base_event(event_id="evt-2", data={"summary": "b2"}),
                ],
            )

            events, diagnostics = load_event_files_with_diagnostics([first, second])
            self.assertEqual(len(events), 2)
            self.assertEqual(len(diagnostics), 1)
            self.assertEqual(diagnostics[0].count, 2)

    def test_empty_paths_still_raises(self) -> None:
        with self.assertRaises(InputError):
            load_event_files_with_diagnostics([])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_workflow -v`
Expected: `FAIL` / `ERROR` — `load_event_files_with_diagnostics` does not exist
(`ImportError`).

- [ ] **Step 3: Implement `load_event_files_with_diagnostics`**

In `src/loopmetry/workflow.py`:
1. Add `from .adapters.base import Diagnostic` to the imports.
2. Add `from .event_merge import EventConflictError, merge_events, merge_events_tolerant`
   (extend the existing `event_merge` import line).
3. Add the new function directly below `load_event_files`:

```python
def load_event_files_with_diagnostics(
    paths: Iterable[str | Path],
) -> tuple[list[Event], tuple[Diagnostic, ...]]:
    """Like load_event_files, but a content conflict under a shared event_id
    becomes an aggregated adapter_conflict Diagnostic (first observation kept)
    instead of raising InputError. Used only by run --source auto (D-011):
    cross-source disagreements must stay visible, never abort the run.
    """

    materialized = [Path(path).expanduser() for path in paths]
    if not materialized:
        raise InputError(
            "no normalized event files were found; pass --input or configure Loopmetry hooks"
        )

    by_id: dict[str, Event] = {}
    conflicted_ids: list[str] = []
    for path in materialized:
        for event in load_jsonl(path):
            existing = by_id.get(event.event_id)
            if existing is None:
                by_id[event.event_id] = event
                continue
            merged, conflicted = merge_events_tolerant(existing, event)
            by_id[event.event_id] = merged
            if conflicted:
                conflicted_ids.append(event.event_id)

    diagnostics: tuple[Diagnostic, ...] = ()
    if conflicted_ids:
        shown = ", ".join(conflicted_ids[:5])
        if len(conflicted_ids) > 5:
            shown += f", +{len(conflicted_ids) - 5} more"
        diagnostics = (
            Diagnostic(
                kind="adapter_conflict",
                summary=f"conflicting duplicate event_id(s) across sources, first observation kept: {shown}",
                count=len(conflicted_ids),
            ),
        )

    events = sorted(by_id.values(), key=lambda event: (event.timestamp, event.event_id))
    return events, diagnostics
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_workflow -v`
Expected: `OK`, including all pre-existing `test_workflow.py` tests (regression
check — `load_event_files` itself must be untouched).

- [ ] **Step 5: Commit**

```bash
git add src/loopmetry/workflow.py tests/test_workflow.py
git commit -m "feat: add load_event_files_with_diagnostics for tolerant cross-source merge"
```

---

### Task 3: `run_participant_workflow(strict=...)` and manifest `source_coverage`

**Files:**
- Modify: `src/loopmetry/workflow.py`
- Test: `tests/test_workflow.py`

**Interfaces:**
- Consumes: `load_event_files_with_diagnostics` (Task 2); `CaptureMode` from
  `loopmetry.schema` (`CaptureMode.HISTORY_BACKFILL` member).
- Produces:
  - `RunArtifacts.source_diagnostics: tuple[Diagnostic, ...] = ()` (new field,
    appended after the existing `receipt` field to keep positional construction
    in any existing call sites safe).
  - `run_participant_workflow(..., strict: bool = True) -> RunArtifacts` — when
    `strict=False`, uses the tolerant loader and populates
    `RunArtifacts.source_diagnostics`; `manifest.json` gains a `source_coverage`
    key (`{"mode": "auto", "history_included": bool, "diagnostics": [...]}`)
    only when `strict=False`. When `strict=True` (default), output is
    byte-for-byte what it is today — no `source_coverage` key at all.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_workflow.py`:

```python
class StrictFlagTests(unittest.TestCase):
    def test_strict_true_default_manifest_has_no_source_coverage_key(self) -> None:
        source = ROOT / "examples" / "demo_project.jsonl"
        with tempfile.TemporaryDirectory() as directory:
            artifacts = run_participant_workflow(
                [source],
                assignment_id="course-2026",
                submitter_id="S001",
                output_root=Path(directory) / "runs",
            )
            self.assertEqual(artifacts.source_diagnostics, ())
            manifest = json.loads(artifacts.manifest_json.read_text(encoding="utf-8"))
            self.assertNotIn("source_coverage", manifest)

    def test_strict_false_tolerates_conflict_and_reports_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.jsonl"
            second = root / "b.jsonl"
            _write_jsonl(
                first,
                [
                    _base_event(
                        provenance=[
                            {
                                "source": "claude-code",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ],
                        data={"summary": "from-hook"},
                    )
                ],
            )
            _write_jsonl(
                second,
                [
                    _base_event(
                        provenance=[
                            {
                                "source": "claude-code",
                                "capture_mode": "history-backfill",
                                "adapter_version": "1.0.0",
                            }
                        ],
                        data={"summary": "from-history"},
                    )
                ],
            )

            artifacts = run_participant_workflow(
                [first, second],
                assignment_id="course-2026",
                submitter_id="S001",
                output_root=root / "runs",
                strict=False,
            )
            self.assertEqual(len(artifacts.source_diagnostics), 1)
            self.assertEqual(artifacts.source_diagnostics[0].kind, "adapter_conflict")
            manifest = json.loads(artifacts.manifest_json.read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_coverage"]["mode"], "auto")
            self.assertTrue(manifest["source_coverage"]["history_included"])
            self.assertEqual(len(manifest["source_coverage"]["diagnostics"]), 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_workflow -v`
Expected: `FAIL` — `run_participant_workflow() got an unexpected keyword
argument 'strict'` (and `RunArtifacts` has no `source_diagnostics`).

- [ ] **Step 3: Implement the `strict` flag and manifest change**

In `src/loopmetry/workflow.py`:

1. Add `from .schema import CaptureMode, Event` (extend the existing `.schema`
   import line, which currently only imports `Event`).
2. Add the new field to `RunArtifacts`, after `receipt`:

```python
    receipt: SubmissionReceipt | None = None
    source_diagnostics: tuple[Diagnostic, ...] = ()
```

3. Add `source_coverage: Mapping[str, Any] | None = None` to
   `_write_run_manifest`'s signature (add `Any, Mapping` to the `typing` import
   line at the top of the file), and only add the key when it's not `None`:

```python
def _write_run_manifest(
    path: Path,
    *,
    run_id: str,
    project_id: str,
    assignment_id: str,
    submitter_id: str,
    source_files: Sequence[Path],
    receipt: SubmissionReceipt | None,
    source_coverage: Mapping[str, Any] | None = None,
) -> None:
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "project_id": project_id,
        "assignment_id": assignment_id,
        "submitter_id": submitter_id,
        "source_files": [
            {
                "name": source.name,
                "sha256": _sha256_file(source),
            }
            for source in source_files
        ],
        "receipt": receipt.to_mapping() if receipt else None,
    }
    if source_coverage is not None:
        manifest["source_coverage"] = source_coverage
    write_private_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
```

4. Update `run_participant_workflow`'s signature and body:

```python
def run_participant_workflow(
    source_files: Sequence[str | Path],
    *,
    assignment_id: str,
    submitter_id: str,
    project_id: str | None = None,
    output_root: str | Path = ".loopmetry/runs",
    server_url: str | None = None,
    submission_token: str | None = None,
    timeout_seconds: float = 30.0,
    strict: bool = True,
) -> RunArtifacts:
    """Analyze, render, package, and optionally upload one participant run.

    strict=False switches to the tolerant cross-source loader (D-011): a
    content conflict between, e.g., a hook observation and a history-backfill
    observation of the same event_id becomes an adapter_conflict diagnostic
    instead of aborting the run. Only run --source auto passes strict=False;
    every other caller keeps today's hard-fail-on-conflict behavior.
    """

    normalized_sources = [Path(path).expanduser() for path in source_files]
    if strict:
        loaded_events = load_event_files(normalized_sources)
        source_diagnostics: tuple[Diagnostic, ...] = ()
    else:
        loaded_events, source_diagnostics = load_event_files_with_diagnostics(normalized_sources)
    events = select_project(loaded_events, project_id)
    report = ProjectEvaluator().evaluate(events)
    created_at = _utc_now()
    run_id = _run_id(created_at)
    run_directory = Path(output_root).expanduser() / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    try:
        run_directory.chmod(0o700)
    except OSError:
        pass

    report_json = write_private_text(run_directory / "report.json", render(report, "json") + "\n")
    report_html = write_private_text(run_directory / "report.html", render(report, "html"))
    submission = build_submission(
        report,
        events,
        assignment_id=assignment_id,
        submitter_id=submitter_id,
        source_file_count=len(normalized_sources),
        created_at=created_at,
        run_id=run_id,
    )
    submission_json = write_private_text(
        run_directory / "submission.json",
        render_submission(submission),
    )

    source_coverage: dict[str, Any] | None = None
    if not strict:
        history_included = any(
            record.capture_mode is CaptureMode.HISTORY_BACKFILL
            for event in events
            for record in event.provenance
        )
        source_coverage = {
            "mode": "auto",
            "history_included": history_included,
            "diagnostics": [
                {"kind": d.kind, "summary": d.summary, "count": d.count}
                for d in source_diagnostics
            ],
        }

    receipt: SubmissionReceipt | None = None
    manifest_json = run_directory / "manifest.json"
    _write_run_manifest(
        manifest_json,
        run_id=run_id,
        project_id=report.project_id,
        assignment_id=assignment_id,
        submitter_id=submitter_id,
        source_files=normalized_sources,
        receipt=None,
        source_coverage=source_coverage,
    )

    if server_url is not None:
        if not submission_token:
            raise SubmissionError("submission_token is required when server_url is set")
        receipt = submit_envelope(
            server_url,
            submission_token,
            submission,
            timeout_seconds=timeout_seconds,
        )
        write_private_text(
            run_directory / "receipt.json",
            json.dumps(receipt.to_mapping(), ensure_ascii=False, indent=2) + "\n",
        )
        _write_run_manifest(
            manifest_json,
            run_id=run_id,
            project_id=report.project_id,
            assignment_id=assignment_id,
            submitter_id=submitter_id,
            source_files=normalized_sources,
            receipt=receipt,
            source_coverage=source_coverage,
        )
    return RunArtifacts(
        run_id=run_id,
        run_directory=run_directory,
        report_json=report_json,
        report_html=report_html,
        submission_json=submission_json,
        manifest_json=manifest_json,
        report=report,
        submission=submission,
        receipt=receipt,
        source_diagnostics=source_diagnostics,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_workflow -v`
Expected: `OK`, all tests including every pre-existing one in the file.

- [ ] **Step 5: Commit**

```bash
git add src/loopmetry/workflow.py tests/test_workflow.py
git commit -m "feat: add strict flag to run_participant_workflow and source_coverage manifest block"
```

---

### Task 4: Extract shared history-import consent helper in `cli.py`

**Files:**
- Modify: `src/loopmetry/cli.py`
- Test: existing `tests/test_cli.py` `CliTests`/`HistoryConsentTests` (regression
  only — no new tests in this task; Task 6 adds the new `run --source auto`
  tests once the helper is wired into `run` too)

**Interfaces:**
- Produces: `_consented_history_import(adapter, context, root, *, interactive:
  bool, output_path: Path) -> AdapterRun | None` — runs the interactive
  double-prompt (if `interactive`), discovers/previews/imports, merges into
  `output_path` (fail-closed on conflict/corrupt existing content, exactly as
  today), saves the checkpoint, prints the new-count/diagnostics/coverage
  summary, and returns the `AdapterRun`. Returns `None` if the interactive user
  declines either prompt (caller should treat that as "nothing imported," not
  an error). Callers are responsible for the *non-interactive* consent gate
  (hard error for `history import`, silent skip for `run --source auto`)
  *before* calling this function — this function does not know which caller it
  serves.

This task is a **pure refactor**: `_run_history`'s `import` branch must behave
identically before and after. No behavior change, no new CLI flags yet (Task 5
adds those).

- [ ] **Step 1: Confirm current behavior is captured by existing tests**

Run: `uv run python -m unittest tests.test_cli -v -k History`
Expected: `OK` — note the passing count; this is the regression baseline for
this task.

- [ ] **Step 2: Extract `_consented_history_import`**

In `src/loopmetry/cli.py`, add this function above `_run_history` (it needs
`AdapterRun` — add it to the existing `from .adapters.base import AdapterError,
DiscoveryContext` line):

```python
def _consented_history_import(
    adapter: SourceAdapter,
    context: DiscoveryContext,
    root: Path,
    *,
    interactive: bool,
    output_path: Path,
) -> AdapterRun | None:
    """Runs the interactive double-confirmation, then discovers, previews,
    imports, merges into output_path, and saves the checkpoint.

    Callers own the non-interactive consent gate before calling this: history
    import hard-fails without --yes, while run --source auto silently skips
    the call entirely. Once called, the interactive prompts below are asked
    unconditionally when interactive=True, matching history import's existing
    behavior regardless of any consent flag.
    """
    if interactive:
        scan_answer = input(
            "Scan local Claude Code history for this project to preview "
            "importable sessions? [y/N] "
        ).strip().lower()
        if scan_answer != "y":
            print("import cancelled")
            return None

    candidates = adapter.discover(context)
    preview = adapter.preview(candidates)

    if interactive:
        print(
            f"{preview.session_count} session(s), {preview.total_size_bytes} byte(s) "
            "of local Claude Code history will be read."
        )
        answer = input("Proceed with import? [y/N] ").strip().lower()
        if answer != "y":
            print("import cancelled")
            return None

    try:
        checkpoint = load_checkpoint(root, adapter.name)
    except AdapterError as exc:
        print(f"warning: {exc}; re-importing without a checkpoint", file=sys.stderr)
        checkpoint = None

    run = adapter.import_candidates(candidates, context, checkpoint=checkpoint)

    # Fail closed: a corrupt or unparsable existing output file must never be
    # treated as "no prior evidence" and silently overwritten with only this
    # run's events (that would delete everything previously imported). The
    # checkpoint from this run has not been saved yet, so raising here leaves
    # both the output file and the checkpoint untouched -- safe to retry once
    # the file is fixed or removed by hand.
    existing_events = load_jsonl(output_path) if output_path.exists() else []

    by_id: dict[str, Event] = {event.event_id: event for event in existing_events}
    for event in run.events:
        existing = by_id.get(event.event_id)
        if existing is None:
            by_id[event.event_id] = event
            continue
        # Overlapping observations merge without losing provenance (invariant
        # 10); a genuine content conflict under the same event_id is an error,
        # matching io.load_jsonl and EventStore.add_events elsewhere.
        try:
            by_id[event.event_id] = merge_events(existing, event)
        except EventConflictError as exc:
            raise InputError(
                f"{output_path}: conflicting duplicate event_id {event.event_id!r}"
            ) from exc
    merged_events = sorted(by_id.values(), key=lambda event: (event.timestamp, event.event_id))
    _write_events_atomically(output_path, merged_events)

    if run.checkpoint is not None:
        save_checkpoint(root, run.checkpoint)

    new_count = len(by_id) - len(existing_events)
    diagnostic_summary = ", ".join(f"{d.kind}={d.count}" for d in run.diagnostics) or "none"
    print(f"imported {new_count} new event(s); {len(merged_events)} total in {output_path}")
    print(f"diagnostics: {diagnostic_summary}")
    print(f"coverage: {run.coverage.to_mapping()['categories']}")
    return run
```

`SourceAdapter` and `AdapterRun` need importing: change the existing
`from .adapters.base import AdapterError, DiscoveryContext` line to
`from .adapters.base import AdapterError, AdapterRun, DiscoveryContext, SourceAdapter`.

- [ ] **Step 3: Replace `_run_history`'s import branch body with a call to the helper**

Replace the `if args.history_command == "import":` block's body (everything from
the consent-check comment through the final `print(f"coverage: ...")` call,
i.e. the code currently at approximately lines 563–648) with:

```python
    if args.history_command == "import":
        # Consent must be checked before any transcript content is read at all --
        # discover() itself opens and JSON-parses the first lines of every
        # candidate file to confirm its cwd, so calling it before this check
        # would mean a rejected non-interactive run had already read local
        # history.
        if not interactive and not args.yes:
            raise InputError(
                "loopmetry history import requires --yes when not run interactively "
                "(this flag is the explicit consent to read local history)"
            )

        output_path = (
            Path(args.output).expanduser()
            if args.output
            else root / ".loopmetry" / "events" / "claude-code-history.jsonl"
        )
        _consented_history_import(adapter, context, root, interactive=interactive, output_path=output_path)
        return 0
```

(The old code's two `print("import cancelled"); return 0` branches are now
handled inside `_consented_history_import`, which returns `None` in both
cases; `_run_history` doesn't need to inspect that return value since it always
returns `0` either way, matching today's behavior exactly.)

- [ ] **Step 4: Run the regression tests**

Run: `uv run python -m unittest tests.test_cli -v -k History`
Expected: `OK`, same test count and names as Step 1 — confirms the refactor
changed no observable behavior.

- [ ] **Step 5: Run the full test suite and ruff**

Run: `uv run python -m unittest discover -s tests -v && uvx --from ruff==0.12.12 ruff check .`
Expected: `OK` for the suite; no ruff findings.

- [ ] **Step 6: Commit**

```bash
git add src/loopmetry/cli.py
git commit -m "refactor: extract _consented_history_import for reuse by run --source auto"
```

---

### Task 5: Wire `run --source auto` into the CLI

**Files:**
- Modify: `src/loopmetry/cli.py`

**Interfaces:**
- Consumes: `_consented_history_import` (Task 4); `run_participant_workflow(...,
  strict=...)` (Task 3); `ClaudeCodeHistoryAdapter` (already imported at the
  top of `cli.py`).
- Produces: new `run` subparser arguments `--source {auto}`, `--since`,
  `--until`, `--include-history`; `_auto_source_files(args, root) -> list[Path]`;
  `_maybe_import_history_for_auto(args, root) -> None`; updated `_run(args)`
  `"run"` branch.

- [ ] **Step 1: Add the new `run` subparser arguments**

In `cli.py`'s `_build_parser` (or equivalent function containing the existing
`run = subparsers.add_parser("run", ...)` block), immediately after the
existing `run.add_argument("--timeout", ...)` line, add:

```python
    run.add_argument(
        "--source",
        choices=("auto",),
        default=None,
        help=(
            "Use 'auto' to merge hook, explicit, and consented Claude Code "
            "history evidence (default: hook/explicit only, unchanged)."
        ),
    )
    run.add_argument(
        "--since", default=None, help="YYYY-MM-DD lower bound for --source auto history discovery."
    )
    run.add_argument(
        "--until", default=None, help="YYYY-MM-DD upper bound for --source auto history discovery."
    )
    run.add_argument(
        "--include-history",
        action="store_true",
        help="Consent to reading local Claude Code history non-interactively under --source auto.",
    )
```

- [ ] **Step 2: Add `_parse_until`**

Directly below the existing `_parse_since` function, add:

```python
def _parse_until(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise InputError(f"--until must be YYYY-MM-DD, got {value!r}") from exc
```

- [ ] **Step 3: Add `_auto_source_files` and `_maybe_import_history_for_auto`**

Directly below `_participant_source_files`, add:

```python
def _auto_source_files(args: argparse.Namespace, root: Path) -> list[Path]:
    discovered = discover_event_files(root)
    explicit = [Path(path).expanduser() for path in args.input]
    combined = {path.resolve() for path in (*discovered, *explicit)}
    if not combined:
        raise InputError(
            f"no Loopmetry event files found below {root}, and no history was imported; "
            "pass --input, configure capture hooks, or check --include-history"
        )
    return sorted(combined)


def _maybe_import_history_for_auto(args: argparse.Namespace, root: Path) -> None:
    interactive = sys.stdin.isatty()
    if not interactive and not args.include_history:
        # Non-interactive run --source auto without explicit consent: proceed
        # with hook/explicit evidence only. Unlike history import, this is not
        # an error -- run is the one-command path and must not abort a routine
        # analysis over an omitted optional flag (roadmap milestone 2 slice 4).
        return
    context = DiscoveryContext(
        project_root=root,
        since=_parse_since(args.since),
        until=_parse_until(args.until),
        interactive=interactive,
    )
    claude_home_raw = os.environ.get(DEFAULT_CLAUDE_HOME_ENV)
    claude_home = Path(claude_home_raw).expanduser() if claude_home_raw else None
    adapter = ClaudeCodeHistoryAdapter(claude_home=claude_home)
    output_path = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
    _consented_history_import(adapter, context, root, interactive=interactive, output_path=output_path)
```

- [ ] **Step 4: Wire it into `_run(args)`'s `"run"` branch**

Replace the current `if args.command == "run":` block:

```python
    if args.command == "run":
        assignment_id, submitter_id = _required_run_identity(args)
        source_files = _participant_source_files(args)
        token = token_from_environment(args.token_env) if args.server else None
        artifacts = run_participant_workflow(
            source_files,
            assignment_id=assignment_id,
            submitter_id=submitter_id,
            project_id=args.project_id,
            output_root=args.output_root,
            server_url=args.server,
            submission_token=token,
            timeout_seconds=args.timeout,
        )
        print(f"analysis complete: project={artifacts.report.project_id} run={artifacts.run_id}")
        print(f"HTML report: {artifacts.report_html}")
        print(f"submission file: {artifacts.submission_json}")
        if artifacts.receipt:
            duplicate = " duplicate" if artifacts.receipt.duplicate else ""
            print(
                f"uploaded:{duplicate} submission={artifacts.receipt.submission_id} "
                f"attempt={artifacts.receipt.attempt} status={artifacts.receipt.status}"
            )
        else:
            print("upload skipped: no --server was configured")
        return 0
```

with:

```python
    if args.command == "run":
        assignment_id, submitter_id = _required_run_identity(args)
        if args.source == "auto":
            root = Path(args.root).expanduser()
            _maybe_import_history_for_auto(args, root)
            source_files = _auto_source_files(args, root)
            strict = False
        else:
            source_files = _participant_source_files(args)
            strict = True
        token = token_from_environment(args.token_env) if args.server else None
        artifacts = run_participant_workflow(
            source_files,
            assignment_id=assignment_id,
            submitter_id=submitter_id,
            project_id=args.project_id,
            output_root=args.output_root,
            server_url=args.server,
            submission_token=token,
            timeout_seconds=args.timeout,
            strict=strict,
        )
        print(f"analysis complete: project={artifacts.report.project_id} run={artifacts.run_id}")
        print(f"HTML report: {artifacts.report_html}")
        print(f"submission file: {artifacts.submission_json}")
        if artifacts.source_diagnostics:
            summary = ", ".join(f"{d.kind}={d.count}" for d in artifacts.source_diagnostics)
            print(f"source diagnostics: {summary}")
        if artifacts.receipt:
            duplicate = " duplicate" if artifacts.receipt.duplicate else ""
            print(
                f"uploaded:{duplicate} submission={artifacts.receipt.submission_id} "
                f"attempt={artifacts.receipt.attempt} status={artifacts.receipt.status}"
            )
        else:
            print("upload skipped: no --server was configured")
        return 0
```

- [ ] **Step 5: Run the full suite and ruff**

Run: `uv run python -m unittest discover -s tests -v && uvx --from ruff==0.12.12 ruff check .`
Expected: `OK`; no ruff findings. (No new tests exist yet for the auto path —
Task 6 adds them. This step just confirms nothing existing broke and the module
imports/parses cleanly.)

- [ ] **Step 6: Manual smoke test**

```bash
uv run loopmetry run --input examples/demo_project.jsonl \
  --assignment-id demo --submitter-id local --output-root /tmp/loopmetry-smoke-default
uv run loopmetry run --source auto --input examples/demo_project.jsonl \
  --assignment-id demo --submitter-id local --output-root /tmp/loopmetry-smoke-auto \
  --include-history </dev/null
```

Expected: both succeed with `analysis complete: ...`; the second prints
`upload skipped: no --server was configured` and no `source diagnostics:` line
(no conflicts, no local Claude Code history fixture present to import in this
manual check). Confirm `/tmp/loopmetry-smoke-auto/*/manifest.json` contains a
`source_coverage` key and `/tmp/loopmetry-smoke-default/*/manifest.json` does
not.

- [ ] **Step 7: Commit**

```bash
git add src/loopmetry/cli.py
git commit -m "feat: wire loopmetry run --source auto into the CLI"
```

---

### Task 6: CLI tests for `run --source auto`

**Files:**
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `run --source auto` CLI surface (Task 5); the existing
  `_make_history_project` helper already defined in both `CliTests` and
  `HistoryConsentTests` in `tests/test_cli.py`.

- [ ] **Step 1: Write the new tests**

Add a new test class to `tests/test_cli.py`, after `HistoryConsentTests`:

```python
class RunAutoSourceTests(unittest.TestCase):
    def run_cli(
        self,
        *args: str,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "loopmetry", *args],
            cwd=ROOT,
            text=True,
            input=stdin,
            capture_output=True,
            check=False,
            env=env,
        )

    def _make_history_project(self, tmp: Path) -> tuple[Path, Path]:
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
        (project_dir / "sess.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        return root, claude_home

    def test_default_run_is_unaffected_by_new_flags_being_absent(self) -> None:
        source = str(ROOT / "examples" / "demo_project.jsonl")
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "runs"
            result = self.run_cli(
                "run", "--input", source,
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            run_dir = next(output_root.iterdir())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn("source_coverage", manifest)
            self.assertNotIn("source diagnostics", result.stdout)

    def test_auto_non_interactive_without_include_history_skips_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples" / "demo_project.jsonl", hooks / "claude-code.jsonl")
            output_root = root / "runs"
            result = self.run_cli(
                "run", "--source", "auto", "--root", str(root),
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / ".loopmetry" / "events" / "claude-code-history.jsonl").exists())

    def test_auto_non_interactive_with_include_history_imports_and_merges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples" / "demo_project.jsonl", hooks / "claude-code.jsonl")
            output_root = root / "runs"
            result = self.run_cli(
                "run", "--source", "auto", "--root", str(root), "--include-history",
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            history_output = root / ".loopmetry" / "events" / "claude-code-history.jsonl"
            self.assertTrue(history_output.exists())
            run_dir = next(output_root.iterdir())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_coverage"]["mode"], "auto")
            self.assertTrue(manifest["source_coverage"]["history_included"])

    def test_auto_conflict_between_hook_and_history_is_reported_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            # A conflicting duplicate: same event_id as whatever the history
            # adapter derives for this session's first note event, but this
            # slice doesn't need to predict that ID -- instead, prove the
            # tolerant path activates end-to-end by pre-seeding the history
            # output file with a record that conflicts with a hook event that
            # shares its event_id.
            conflicting_id = "manual-conflict-1"
            (hooks / "claude-code.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "event_id": conflicting_id,
                        "project_id": "demo-expense-cli",
                        "session_id": "sess-1",
                        "timestamp": "2026-08-20T09:00:00Z",
                        "type": "note",
                        "actor": "system",
                        "source": "claude-code",
                        "data": {"summary": "from-hook"},
                        "provenance": [
                            {
                                "source": "claude-code",
                                "capture_mode": "hook",
                                "adapter_version": "1.0.0",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            events_dir = root / ".loopmetry" / "events"
            events_dir.mkdir(parents=True)
            (events_dir / "preexisting-history.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "0.2",
                        "event_id": conflicting_id,
                        "project_id": "demo-expense-cli",
                        "session_id": "sess-1",
                        "timestamp": "2026-08-20T09:00:00Z",
                        "type": "note",
                        "actor": "system",
                        "source": "claude-code",
                        "data": {"summary": "from-history"},
                        "provenance": [
                            {
                                "source": "claude-code",
                                "capture_mode": "history-backfill",
                                "adapter_version": "1.0.0",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_root = root / "runs"
            result = self.run_cli(
                "run", "--source", "auto", "--root", str(root),
                "--assignment-id", "course-2026", "--submitter-id", "S001",
                "--output-root", str(output_root),
                env={**os.environ, "LOOPMETRY_CLAUDE_HOME": str(claude_home)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("source diagnostics: adapter_conflict=1", result.stdout)
            run_dir = next(output_root.iterdir())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_coverage"]["diagnostics"][0]["kind"], "adapter_conflict")

    def test_auto_interactive_prompts_are_reused_from_history_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples" / "demo_project.jsonl", hooks / "claude-code.jsonl")
            output_root = root / "runs"
            with (
                mock.patch("builtins.input", side_effect=["y", "y"]) as mock_input,
                mock.patch("sys.stdin.isatty", return_value=True),
                mock.patch.dict(os.environ, {"LOOPMETRY_CLAUDE_HOME": str(claude_home)}),
            ):
                exit_code = main(
                    [
                        "run", "--source", "auto", "--root", str(root),
                        "--assignment-id", "course-2026", "--submitter-id", "S001",
                        "--output-root", str(output_root),
                    ]
                )
            self.assertEqual(exit_code, 0)
            self.assertEqual(mock_input.call_count, 2)
            self.assertTrue((root / ".loopmetry" / "events" / "claude-code-history.jsonl").exists())

    def test_since_and_until_are_passed_to_history_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, claude_home = self._make_history_project(Path(tmp))
            hooks = root / ".loopmetry" / "hooks"
            hooks.mkdir(parents=True)
            shutil.copyfile(ROOT / "examples" / "demo_project.jsonl", hooks / "claude-code.jsonl")
            output_root = root / "runs"
            with (
                mock.patch.object(
                    ClaudeCodeHistoryAdapter, "discover", autospec=True, return_value=()
                ) as mock_discover,
                mock.patch.dict(os.environ, {"LOOPMETRY_CLAUDE_HOME": str(claude_home)}),
            ):
                exit_code = main(
                    [
                        "run", "--source", "auto", "--root", str(root), "--include-history",
                        "--since", "2026-08-01", "--until", "2026-08-31",
                        "--assignment-id", "course-2026", "--submitter-id", "S001",
                        "--output-root", str(output_root),
                    ]
                )
            self.assertEqual(exit_code, 0)
            mock_discover.assert_called_once()
            context = mock_discover.call_args.args[1]
            self.assertEqual(context.since.strftime("%Y-%m-%d"), "2026-08-01")
            self.assertEqual(context.until.strftime("%Y-%m-%d"), "2026-08-31")
```

This requires two new imports at the top of `tests/test_cli.py`:
`import shutil` and `from unittest import mock` — check first whether they're
already imported (search the file's import block); `mock` already is (used by
`HistoryConsentTests`), `shutil` is not, so add it.

- [ ] **Step 2: Run the new tests**

Run: `uv run python -m unittest tests.test_cli -v -k RunAutoSource`
Expected: `OK` (6 tests). Tasks 4–5 already implemented the CLI surface these
tests exercise, so this step is a verification run rather than a red/green
cycle — if any test fails here, the bug is in Task 4 or 5's implementation,
not a missing feature.

- [ ] **Step 3: Run the full suite and ruff**

Run: `uv run python -m unittest discover -s tests -v && uvx --from ruff==0.12.12 ruff check .`
Expected: `OK`; no ruff findings.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py
git commit -m "test: cover loopmetry run --source auto consent, merge, and conflict paths"
```

---

### Task 7: Documentation and decision log

**Files:**
- Modify: `docs/hook-capture.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/decision-log.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update `docs/hook-capture.md`**

In the "One-command analysis and submission" section (after the existing
`loopmetry run \ --assignment-id agent-ai-2026 \ --submitter-id S001` example),
add:

```markdown
`--source auto` additionally triggers a consented Claude Code history scan as
part of the same command, merging it with hook and explicit evidence:

```bash
loopmetry run --source auto --since 2026-08-01 --until 2026-08-31 \
  --assignment-id agent-ai-2026 --submitter-id S001
```

In an interactive terminal this asks the same two confirmation questions as
`loopmetry history import` (scan, then proceed with N sessions). In a
non-interactive shell, history is included only when `--include-history` is
passed; its absence is not an error — the run proceeds with hook and explicit
evidence only, since `run` is the one-command path and must not abort over an
omitted optional flag. `--since`/`--until` (`YYYY-MM-DD`) bound the history scan
for this invocation only; there is no default assignment-window bound yet (that
part of decision D-012 remains blocked on assignment-schema work — see
`docs/decision-log.md` D-015).

Cross-source disagreements — the same `event_id` observed with different
content from, say, a hook and a history-backfill import — never abort a
`--source auto` run. They surface as an `adapter_conflict` diagnostic (first
observation kept) printed to stdout as `source diagnostics: adapter_conflict=N`
and recorded in `manifest.json`'s `source_coverage` block:

```json
{
  "source_coverage": {
    "mode": "auto",
    "history_included": true,
    "diagnostics": [
      {"kind": "adapter_conflict", "summary": "...", "count": 1}
    ]
  }
}
```

`report.json`/`report.html` do not yet surface source coverage — that is
roadmap milestone 2 slice 6's job. Without `--source auto`, `run`'s behavior,
output, and `manifest.json` shape are unchanged from before this feature.
```

- [ ] **Step 2: Update `docs/roadmap.md`**

Change the slice 4 bullet's opening from:

```markdown
4. **Hybrid auto mode.** `loopmetry run --source auto`: merge hook, explicit, and
```

to:

```markdown
4. **Hybrid auto mode.** *(implemented)* `loopmetry run --source auto`: merge hook, explicit, and
```

(matching the `*(implemented)*` marker style already used for slices 1–3 in
this file).

- [ ] **Step 3: Add decision log entry D-015**

In `docs/decision-log.md`, after the D-014 entry's `**Related:**` line and
before the `---` separator that precedes "## Adding or changing a decision",
add:

```markdown
---

## D-015 — Hybrid auto-merge tolerates cross-source conflicts as diagnostics; assignment-window default remains blocked

**Status:** Accepted
**Context:** Milestone 2 slice 4 (`docs/roadmap.md`) adds `loopmetry run --source auto`, which actively triggers a consented Claude Code history import as part of the one-command flow and merges it with hook/explicit evidence. Two open questions from D-011/D-012 needed resolving before implementation: (1) `merge_events` (`event_merge.py`) raises `EventConflictError` on any same-`event_id` content disagreement, which is correct for same-adapter merges (`history import`, plain `run`, `ingest` — a conflict there more likely means corruption) but wrong for a legitimate cross-source disagreement, where D-011 requires conflicts to stay visible, not crash the run; (2) D-012 said the default backfill window should be the assignment's configured start/end, but `admin_storage.py` has no `Assignment` entity or `starts_at`/`ends_at` field, and D-012 itself only committed to that default "once the administrator schema carries them."
**Decision:** A new `merge_events_tolerant` and `load_event_files_with_diagnostics` (used only when `run_participant_workflow(..., strict=False)`, which only `run --source auto` passes) turn a cross-source content conflict into an aggregated `adapter_conflict` `Diagnostic` — first observation kept, printed to stdout and recorded in `manifest.json`'s `source_coverage` block — instead of raising. `load_event_files` and `merge_events` themselves are unchanged; every other caller keeps hard-failing on conflict. `run --source auto` accepts explicit `--since`/`--until` bounds for the history scan; no assignment-window default is implemented in this slice, and this entry records that D-012's assignment-window default stays blocked on assignment-schema work, to be picked up in its own decision when that schema exists. Non-interactive `run --source auto` without `--include-history` is a silent skip of the history step, not an error — unlike `history import --yes`, which still hard-fails non-interactively without consent — because `run` is the one-command participant path and must not abort a routine analysis over an omitted optional flag.
**Consequences:**

- `src/loopmetry/event_merge.py` and `src/loopmetry/workflow.py` gain tolerant variants used only by the new path; no existing caller's behavior changes.
- `manifest.json` gains an optional `source_coverage` key only when `--source auto` was used; default `run` output is byte-for-byte unchanged.
- Per-metric confidence in `metrics_*.py`/`evaluation.py` is untouched by this slice — "lower confidence" from a conflict is satisfied by making it visible to a human reader (stdout, manifest), not by mutating a metric score. Roadmap milestone 2 slice 6 ("Participant report source coverage") owns adding real report sections.
- `cli.py`'s `history import` consent/import body is factored into `_consented_history_import`, shared with `run --source auto`, so the two commands' interactive prompts and merge-into-file semantics cannot silently drift apart.

**Related:** `docs/decision-log.md` D-011, D-012, `docs/roadmap.md` milestone 2 slice 4, `src/loopmetry/event_merge.py`, `src/loopmetry/workflow.py`, `src/loopmetry/cli.py`
```

- [ ] **Step 4: Update `AGENTS.md`'s routing table**

Add a row after the existing "Hook integration installer" row:

```markdown
| Hybrid auto mode | `docs/hook-capture.md`, `docs/decision-log.md` D-015 | `src/loopmetry/cli.py`, `src/loopmetry/workflow.py`, `src/loopmetry/event_merge.py` | `tests/test_cli.py`, `tests/test_workflow.py`, `tests/test_event_merge.py` |
```

- [ ] **Step 5: Commit**

```bash
git add docs/hook-capture.md docs/roadmap.md docs/decision-log.md AGENTS.md
git commit -m "docs: document run --source auto and add decision D-015"
```

---

### Task 8: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full test suite**

Run: `uv run python -m unittest discover -s tests -v`
Expected: `OK`, every test in the suite (pre-existing and new) passes.

- [ ] **Step 2: Lint**

Run: `uvx --from ruff==0.12.12 ruff check .`
Expected: no findings.

- [ ] **Step 3: Lockfile and build**

Run: `uv lock --check && uv build`
Expected: both succeed with no changes needed to `uv.lock` (no new
dependencies were added) and a built wheel/sdist in `dist/`.

- [ ] **Step 4: Manual end-to-end smoke test with a real conflict fixture**

Reuse the scratchpad-style scenario from Task 6's
`test_auto_conflict_between_hook_and_history_is_reported_not_fatal` manually
once, outside the test suite, to visually confirm the stdout line and
`manifest.json` shape look right to a human reader (not just assertions):

```bash
rm -rf /tmp/loopmetry-slice4-manual && mkdir -p /tmp/loopmetry-slice4-manual
uv run loopmetry run --source auto --root /tmp/loopmetry-slice4-manual \
  --assignment-id demo --submitter-id local --output-root /tmp/loopmetry-slice4-manual/runs
cat /tmp/loopmetry-slice4-manual/runs/*/manifest.json
```

Expected: since no hooks/history exist under that empty root, this should
raise `InputError` ("no Loopmetry event files found... and no history was
imported") with exit code 2 — confirming `_auto_source_files`'s empty-result
error path from Task 5 works end-to-end, not just under test mocks.

- [ ] **Step 5: Report status to the user**

Summarize: all tasks complete, full suite green, ruff clean, build/lockfile
clean. Do not open a PR or push without the user's explicit go-ahead (matching
this project's established pattern for slices 1–3).
