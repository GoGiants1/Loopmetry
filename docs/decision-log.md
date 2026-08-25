# Loopmetry decision log

This file records accepted product and architecture decisions. Read it to understand **why** the repository is shaped as it is. It does not prove that a feature is implemented; code and tests are the implementation source of truth.

Statuses:

- **Accepted** — current direction; implementation should conform.
- **Superseded** — retained for history but replaced by a later decision.
- **Proposed** — under discussion and not yet binding.

## Index

| ID | Date | Status | Decision |
|---|---|---|---|
| D-001 | 2026-08-22 | Accepted | Evaluate project workflow, not developer ability |
| D-002 | 2026-08-22 | Accepted | Deterministic evidence core; no universal overall score |
| D-003 | 2026-08-22 | Accepted | Local-first processing and data-minimized submissions |
| D-004 | 2026-08-22 | Accepted | Versioned JSON contracts; renderers do not own evaluation logic |
| D-005 | 2026-08-22 | Accepted | Use `uv`; keep the runtime standard-library-only by default |
| D-006 | 2026-08-22 | Accepted | Static HTML first; React only for multi-run interactive workflows |
| D-007 | 2026-08-22 | Accepted | One-command participant flow and administrator cohort collection are the current milestone |
| D-008 | 2026-08-22 | Accepted | Local LLM execution is deferred; preserve only extension contracts |
| D-009 | 2026-08-22 | Accepted | Submission retries are idempotent and review status is manual |
| D-010 | 2026-08-22 | Accepted | Keep agent instructions navigational; store rationale in this decision log |
| D-011 | 2026-08-23 | Accepted | Prospective hook capture and retrospective historical backfill are both first-class source paths |
| D-012 | 2026-08-23 | Accepted | Hybrid auto mode bounds historical backfill to the assignment window by default; broader operational proposals deferred |
| D-013 | 2026-08-24 | Accepted | Historical backfill checkpoints persist unresolved tool_use state; unknown-status events finalize only when a session has stalled |
| D-014 | 2026-08-25 | Accepted | Hook-config installer merges structurally and gates any existing-file modification behind `--force` with a mandatory backup |
| D-015 | 2026-08-25 | Accepted | Hybrid auto-merge tolerates cross-source conflicts as diagnostics; assignment-window default remains blocked |

---

## D-001 — Evaluate project workflow, not developer ability

**Status:** Accepted
**Context:** Coding-agent traces expose workflow evidence, but they do not establish a person's general engineering ability or employment suitability.
**Decision:** Loopmetry evaluates how project intent becomes plans, changes, verification, recovery, and delivery. It must not rank participants or produce hiring, promotion, compensation, or termination signals.
**Consequences:** Reports use project-level metric cards, confidence, evidence, and gaps. Responsible-use restrictions remain part of the product boundary.
**Related:** `RESPONSIBLE_USE.md`, `docs/metrics.md`

## D-002 — Deterministic evidence core; no universal overall score

**Status:** Accepted
**Context:** Counts, ordering, exit status, and evidence links are reproducible; a single opaque score would collapse distinct constructs and hide uncertainty.
**Decision:** Deterministic metrics remain independent. The same canonical events produce the same results, and every claim carries evidence references, confidence, and measurement gaps. No universal overall developer or participant score is produced.
**Consequences:** Semantic or LLM judgments, when added, remain a separate layer and are not silently blended into deterministic metrics.
**Related:** `docs/event-schema.md`, `docs/metrics.md`, `src/loopmetry/evaluation.py`

## D-003 — Local-first processing and data-minimized submissions

**Status:** Accepted
**Context:** Agent sessions can contain proprietary code, prompts, paths, customer information, and credentials. Administrators need submission status and evaluation results, not the full local transcript.
**Decision:** Raw session material is processed locally. Submission v1 contains compact report and provenance data while excluding raw transcripts, canonical event bodies, source code, secrets, absolute private paths, and unnecessary identity metadata.
**Consequences:** Network transfer is explicit. New submission fields require privacy review, schema versioning, size limits, and tests.
**Related:** `PRIVACY.md`, `SECURITY.md`, `schemas/submission-v1.schema.json`, `src/loopmetry/submission.py`

## D-004 — Versioned JSON contracts; renderers do not own evaluation logic

**Status:** Accepted
**Context:** CLI, HTML, future web UI, and administrator services need a stable boundary without duplicating metric formulas.
**Decision:** JSON and JSON Schema are the integration contracts. Markdown, static HTML, and future React clients render those contracts and must not recompute metrics, submission identity, or review state.
**Consequences:** Contract-breaking changes require an explicit schema version and migration plan.
**Related:** `schemas/`, `src/loopmetry/report.py`, `docs/web-ui.md`

## D-005 — Use `uv`; keep the runtime standard-library-only by default

**Status:** Accepted
**Context:** Participant setup should be reproducible and low-friction, while the CLI and local server should remain portable.
**Decision:** `uv` manages Python versions, locking, commands, and builds. Runtime dependencies remain standard-library-only unless a dependency has a documented security, portability, and maintenance justification.
**Consequences:** Commit `uv.lock`; use `uv sync --locked`, `uv lock --check`, and `uv build` in verification. Do not add parallel dependency-management systems.
**Related:** `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`

## D-006 — Static HTML first; React only for multi-run interactive workflows

**Status:** Accepted
**Context:** A single project report is bounded and read-only; adding a second runtime and duplicated state too early would increase complexity.
**Decision:** Self-contained HTML is the default visualization. Add a React client only when timeline filtering, evidence-graph traversal, cohort comparison, live ingestion, or consent/payload-preview workflows require a persistent interactive UI.
**Consequences:** The future web client consumes versioned JSON/API contracts and never imports Python metric logic.
**Related:** `src/loopmetry/report.py`, `docs/web-ui.md`

## D-007 — One-command participant flow and administrator cohort collection are the current milestone

**Status:** Accepted
**Context:** Participants need a copy-paste command; administrators need visibility across registered submitters, including non-submitters and multiple attempts.
**Decision:** Prioritize: (1) one participant command that discovers or accepts input, evaluates, renders, packages, and optionally submits; and (2) administrator roster, receipt, attempt history, manual review status, and cohort dashboard/export.
**Consequences:** Local artifacts are written before upload, failed uploads are retryable, roster entries exist before submission, and participant ordering is independent of scores.
**Related:** `src/loopmetry/workflow.py`, `src/loopmetry/submission.py`, `src/loopmetry/admin_storage.py`, `src/loopmetry/admin_server.py`, `docs/submission-workflow.md`

## D-008 — Local LLM execution is deferred; preserve only extension contracts

**Status:** Accepted
**Context:** Local model runtimes add installation, capability, schema-following, resource, and support complexity. They are not required to validate the participant/admin workflow.
**Decision:** Keep bounded evaluation-bundle, rubric, provider, and result-schema boundaries, but do not invoke Claude, Codex, Ollama, LM Studio, or another model during participant analysis, submission, or administrator ingestion in the current milestone.
**Consequences:** LLM runtime implementation begins only after deterministic submission contracts and human calibration are stable.
**Related:** `src/loopmetry/llm_bundle.py`, `docs/llm-evaluation.md`, `rubrics/`, `schemas/llm-evaluation-*.json`

## D-009 — Submission retries are idempotent and review status is manual

**Status:** Accepted
**Context:** Network retries must not inflate attempt counts, and metric values must not automatically determine acceptance.
**Decision:** The server identifies identical submissions by content hash and returns the existing receipt. A genuinely new run creates a new attempt. Review states are assigned manually and retained as operational history.
**Consequences:** Token rotation, duplicate retry, attempt ordering, and status-history behavior require tests.
**Related:** `src/loopmetry/submission.py`, `src/loopmetry/admin_storage.py`, `docs/submission-workflow.md`

## D-010 — Keep agent instructions navigational; store rationale in this decision log

**Status:** Accepted
**Context:** Large agent instruction files become stale, duplicate task documents, and make future sessions expensive to orient. Historical rationale is also lost when instructions are rewritten in place.
**Decision:** `AGENTS.md` contains mission boundaries, source-of-truth routing, stable invariants, verification commands, and documentation discipline. Detailed design belongs in task documents; enduring rationale and direction changes belong in this log.
**Consequences:** Future agents start from `AGENTS.md`, use this log for “why,” and inspect code/tests for implementation truth. Superseded decisions remain visible.
**Related:** `AGENTS.md`, `docs/architecture.md`, `docs/roadmap.md`

## D-011 — Support both prospective hook capture and retrospective historical backfill

**Status:** Accepted
**Context:** Hook capture provides privacy-minimized, relatively format-stable forward collection, but it cannot recover work performed before integration. Historical backfill enables zero-setup analysis and recovery of existing Claude Code and Codex sessions, but depends on versioned vendor formats and requires explicit consent for local transcript access.
**Decision:** Hook capture and historical backfill are both first-class source adapters behind one shared `SourceAdapter` contract, and both produce the same canonical event schema. The default participant experience will eventually be a hybrid `loopmetry run --source auto` mode that merges approved hook and history evidence.
**Consequences:**

- neither source path may implement metric semantics;
- every imported event carries source, capture-mode, adapter-version, coverage, and diagnostic provenance (capture modes distinguish at least `hook`, `history-backfill`, `explicit-import`, and `deterministically-derived`);
- overlapping events are deduplicated without discarding provenance; conflicting observations are never silently resolved — they surface as `adapter_conflict` diagnostics and reduce confidence;
- home-directory history discovery is bounded, previewable, and consented; non-interactive runs never read history implicitly;
- raw transcripts are read locally in streaming fashion, are never copied wholesale, and remain excluded from submission v1; and
- hook integration is recommended for prospective capture quality but is not required for analysis.

**Related:** `docs/architecture.md`, `docs/hook-capture.md`, `docs/roadmap.md`, `docs/submission-workflow.md`, `src/loopmetry/hook_capture.py`, future `src/loopmetry/adapters/`

---

## D-012 — Assignment-scoped backfill window; defer broader operational changes

**Status:** Accepted
**Context:** A 2026-08-23 review of how a comparable external tool (local session discovery, attribution, and upload for agent transcripts) handles bounding, caching, and consent surfaced ideas worth weighing against D-011. Most of what it does — field-level provenance merge rather than source-priority, excluding unattributable sessions rather than widening scope, streaming/checkpointed import, no container runtime — already matches D-011 and `AGENTS.md`'s stdlib-only constraint, so no new decision was needed for those. A few ideas were genuinely new. This entry records which were accepted now and which were deliberately deferred, so they are not silently lost or silently adopted.
**Decision:** When milestone 2's hybrid `loopmetry run --source auto` (slice 4) ships, its default historical-backfill time window is the administrator-configured assignment window (start/end), not an unbounded or fixed-lookback default; participants can still widen it explicitly in the preview step. The following related proposals are explicitly deferred, not adopted, pending their own plan-mode design session because they are cross-cutting changes to the storage or auth model:

- replacing long-lived bearer submission tokens with a device-style auth flow and tokenless reruns;
- an outbox/pending-upload-replay model with a stable CLI exit-code contract;
- a layered local cache (adapter / merge / evaluation / future-judge) keyed and reported in the run manifest;
- a thin, version-pinned shell bootstrap (`run.sh`) in front of the Python CLI; and
- an optional Docker-based isolation mode (native `uvx` remains the only supported default execution path — this was already Loopmetry's direction, not a new decision).

**Consequences:**

- slice 4's design must read the assignment's `starts_at`/`ends_at` (once the administrator schema carries them) as the backfill default window;
- this entry is not authorization to build the deferred items; each needs its own plan-mode session before implementation starts; and
- if one of the deferred items is later accepted, it gets its own decision entry rather than rewriting this one.

**Related:** `docs/decision-log.md` D-011, `docs/roadmap.md` milestone 2 slice 4, `docs/submission-workflow.md`

---

## D-013 — Historical backfill checkpoints persist unresolved tool_use state; unknown-status events finalize only when a session has stalled

**Status:** Accepted
**Context:** The slice-2 plan (`docs/superpowers/plans/2026-08-23-claude-code-history-backfill.md`, Task 3/4, written 2026-08-23) originally paired assistant `tool_use` blocks with their later `tool_result` using an in-memory dict scoped to one `_SessionParser` instance, and flushed any still-unresolved Bash call to a `command` event with `status="unknown"` at end-of-stream. Checkpoints (`src/loopmetry/adapters/checkpoints.py`, `Checkpoint.positions`) only stored `{"content_sha256", "records_read"}` — a line-count cursor, not parser state. Reviewed before Task 1 implementation began (2026-08-24), this design had two compounding defects: (1) a `tool_use` flushed to `unknown` at the end of one import has its line counted into `records_read`, so when the real `tool_result` arrives in a later append, the next import resumes past that line with a fresh, empty pending map — the result is silently dropped and the `unknown` status can never be corrected; (2) even if pending state were restored, re-emitting a corrected event under the same `event_id` as the earlier `unknown` placeholder would hit `event_merge.merge_events`'s conflict check (differing `data` under the same ID raises `EventConflictError`, per PR #7's merged dual-source foundation), so "emit unknown now, fix it later" is structurally incompatible with the existing merge contract.
**Decision:** `Checkpoint.positions` per-candidate entries gain a `pending` map (`{tool_use_id: {"record_index", "command", "timestamp"}}`, defaulting to `{}` for backward compatibility) that carries unresolved Bash `tool_use` state across imports. `_SessionParser` seeds its pending map from this stored state instead of starting empty. At end-of-stream, unresolved entries are written back into `pending` unchanged — no event is emitted for them yet. A pending entry is only finalized to a `command(status="unknown")` event (using its originally-stored `record_index`, so the event ID stays stable) when an import observes zero file growth since the position where that entry was left; if the file grew but the entry is still unresolved, it is carried forward again without guessing. Two new diagnostic kinds make both states visible: `unresolved_tool_call` (still pending, not a failure) and `stalled_tool_call` (finalized to `unknown`); both degrade the `commands` coverage category to `partial`, consistent with the plan's existing coverage-degradation rule.
**Consequences:**

- An event ID for a Bash outcome is written to disk exactly once, after either its real `tool_result` is observed or a full import cycle passes with zero growth while it was pending — content under that ID never changes afterward, so no `EventConflictError` is possible from this code path on re-ingest.
- `Checkpoint`/`checkpoint_path`/`load_checkpoint`/`save_checkpoint` need no code changes; `positions` was already an arbitrary per-candidate JSON object.
- The plan document's Task 3 event table, `_SessionParser` prose, and `import_candidates` sketch, and Task 4's incremental-import tests, are revised before Task 1 (`minimize.py` extraction) begins, so the checkpoint-pairing design is implemented correctly the first time.
- Subagent transcripts and Codex sessions inherit the same pending/finalization contract when their adapters are added (slices covering D-011's remaining scope); this is not itself a new decision, just the existing D-011 contract applied consistently.

**Related:** `docs/decision-log.md` D-011, `docs/superpowers/plans/2026-08-23-claude-code-history-backfill.md`, `src/loopmetry/adapters/base.py` (`Checkpoint`), `src/loopmetry/adapters/checkpoints.py`, `src/loopmetry/event_merge.py`

---

## D-014 — Hook-config installer merges structurally and gates any existing-file modification behind `--force` with a mandatory backup

**Status:** Accepted
**Context:** Milestone 2 slice 3 (`docs/roadmap.md`) adds `loopmetry integrate claude-code --preview|--apply|--remove` to generate the `.claude/settings.local.json` hook block documented in `docs/hook-capture.md`, instead of participants copy-pasting it by hand. No prior art for local config merging, backup, or diff preview existed anywhere in `src/loopmetry/` (confirmed by an Explore pass before implementation). A rule was needed for when writing to a file participants may have already customized is safe to do without confirmation, consistent with the fail-closed precedent already established for the historical-backfill adapter (an existing output file that fails to parse is a hard error, never silently treated as empty).
**Decision:** Writing to `.claude/settings.local.json` never requires `--force` when the file does not yet exist (nothing is at risk) or when the computed result is byte-identical to what's already there (a true no-op, e.g. re-running `--apply` after it already succeeded). It requires `--force` whenever the file exists and the write would actually change its content — this rule is uniform across `--apply` and `--remove`, not just `--apply`. Immediately before any such modifying write, the existing bytes are copied to `settings.local.json.bak` (a single backup, overwritten each run, not timestamped). An existing file that is not valid JSON, whose top-level value is not a JSON object, whose `hooks` value is not an object, or whose value for a targeted event is not an array, is a hard error on `--preview`, `--apply`, and `--remove` alike — never silently overwritten or partially applied. Each of the five documented hook events gets **exactly one** managed block: `--apply` replaces that one block in place when its content differs (e.g. `--project-id` changed) instead of adding a second handler, and collapses pre-existing duplicates down to one. Ownership is decided by exact structural match against what this installer would itself generate — a block scoped by an outer `matcher`, or a handler carrying an extra field such as `if`, is never mistaken for full integration and is never touched by `--remove`, since either would fire for only a subset of that event's occurrences. `--remove` is additionally scoped to only the five events this installer manages, never to arbitrary hook events already in the file. Generated handlers use exec form (`"command": "loopmetry"` plus an `"args"` array) rather than a single shell-parsed command string, so a `--project-id` containing spaces or shell metacharacters is passed through as one argument and is never subject to shell word-splitting or injection.
**Consequences:**

- `src/loopmetry/hook_integration.py` holds pure merge/remove/format logic with no I/O, independently unit-tested; `cli.py`'s `_run_integrate` owns reading the existing file, diffing (`difflib`), the force/backup policy, and writing via the existing `atomic_write_bytes` (`src/loopmetry/adapters/checkpoints.py`).
- `--source` accepts only `"claude-code"` for now; Codex hook integration is explicitly a later slice (roadmap milestone 2 slice 5, "Codex parity") and will need its own textual-merge design since no stdlib TOML writer exists — not solved by this entry.
- Because the force/backup rule is uniform across apply and remove, a participant who runs `--remove` on a file they've since hand-edited is protected the same way an `--apply` would be.
- An initial implementation of this entry used a single shell-parsed command string and prefix-based ownership matching; external review before merge found both unsafe (a changed `--project-id` created a second, conflicting hook handler instead of replacing the first, and `--remove` could delete unrelated user-authored hooks matching only by command prefix). Both were corrected before merge, so this entry describes the corrected design directly rather than superseding itself.

**Related:** `docs/decision-log.md` D-011, `docs/hook-capture.md`, `docs/roadmap.md` milestone 2 slice 3, `src/loopmetry/hook_integration.py`, `src/loopmetry/cli.py`, `src/loopmetry/adapters/checkpoints.py`

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

---

## Adding or changing a decision

Append a new entry with a stable ID. Include:

- **Status** and date;
- **Context** — the constraint or problem;
- **Decision** — the chosen direction;
- **Consequences** — required behavior and trade-offs; and
- **Related** paths — contracts, docs, code, and tests.

Do not delete accepted decisions. When direction changes, add a replacement entry and mark the prior one **Superseded** with a link to its replacement. Keep transient debugging notes, action-run failures, and incidents in issues or operational notes rather than this architectural log.
