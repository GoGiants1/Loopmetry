# Roadmap

## Milestone 0 — deterministic evaluator vertical slice

Status: implemented.

- canonical event schema and validation;
- local SQLite evidence store;
- independent metric cards;
- evidence IDs, confidence, and measurement gaps;
- Markdown, JSON, and standalone HTML reports;
- bounded LLM bundle contract;
- `uv` environment and lockfile; and
- synthetic fixtures and tests.

## Milestone 1 — participant command and administrator collection

Status: implemented in v0.3.

- `loopmetry run` one-command local analysis and packaging;
- narrow automatic discovery of Loopmetry hook files;
- content-addressed submission envelope v1;
- retry-safe `loopmetry submit`;
- roster enrollment and token rotation;
- CSV roster import and private credential export;
- idempotent participant upload API;
- repeated-attempt history;
- manual review state and note history;
- complete-roster HTML dashboard, including non-submitters;
- JSON administrator APIs and CSV export; and
- loopback-first deployment and authentication safeguards.

## Milestone 2 — dual-source capture and backfill

Status: current milestone. See decision D-011: prospective hook capture and retrospective historical backfill are both first-class source paths behind one shared adapter contract, converging on a hybrid `loopmetry run --source auto` participant flow.

Ordered slices, each an independent PR:

1. **Shared dual-source foundation.** *(implemented)* Provider-neutral `SourceAdapter` contract and typed models (discovery context, source candidates, import preview, capabilities, incremental checkpoints, adapter runs, coverage report, unparsed-record diagnostics) plus per-event source/capture-mode/adapter-version/coverage provenance (`hook`, `history-backfill`, `explicit-import`, `deterministically-derived`). No behavior change to the existing hook path beyond conforming to the contract.
2. **Claude Code historical backfill.** *(implemented)* Bounded session discovery (project root, session cwd, and an explicit time window), preview with session/event counts and data-size estimate, version-aware parsing, incremental import: `loopmetry history discover|preview|import --source claude-code`. Unattributed or unparsed records become explicit diagnostics, never silent drops. Bash `tool_use`/`tool_result` pairing survives checkpoint boundaries per decision D-013.
3. **Claude Code hook integration.** *(implemented)* `loopmetry integrate claude-code --preview|--apply|--remove` with diff preview, backup, and a `--force` overwrite policy. Integration is recommended prospective capture, not a prerequisite for analysis.
4. **Hybrid auto mode.** `loopmetry run --source auto`: merge hook, explicit, and consented history evidence; deduplicate with provenance preserved; surface `adapter_conflict` diagnostics and a coverage summary. Interactive terminals preview history candidates and ask before import; non-interactive runs use hook and explicit input only unless `--include-history` is passed. Default backfill window is the assignment's configured start/end (decision D-012), not an unbounded or fixed-lookback default.
5. **Codex parity.** *(implemented)* Codex historical adapter (apply-patch file changes and command events with unknown status), Codex hook integration, source/adapter version and coverage matrix, coverage comparability regression tests.
6. **Participant report source coverage.** Report sections for sources observed, sessions included and excluded, hook-versus-backfill coverage per evidence category, unknown records, conflicts, analysis window, and adapter-caused evidence gaps.

Hooks remain the preferred live path. Home-directory transcript scanning must always be explicit, previewed, and bounded; raw transcripts stay local and outside submission v1.

## Milestone 3 — project evidence graph

- requirement and acceptance-criterion import;
- Spec → Task → File → Test → Commit edges;
- Git history correlation;
- issue and pull-request correlation;
- requirement-level completion reports; and
- evidence graph export.

## Milestone 4 — project-volume calibration and human evaluation

- project-size descriptors;
- changed-file and language normalization;
- active-time and repository coverage estimates;
- task-complexity buckets;
- benchmark project manifests;
- consented human annotations;
- inter-rater agreement and sensitivity analysis; and
- documented metric failure cases.

## Milestone 5 — richer administrator operations

- assignment deadlines and late markers;
- batch token rotation and participant deactivation;
- signed receipts;
- server migrations and backup tooling;
- reverse-proxy deployment examples;
- role-based reviewer access or SSO; and
- retention/deletion controls.

## Milestone 6 — React cohort and evidence explorer

Introduce `web/` only when interaction exceeds the server-rendered HTML surface:

- large-cohort filtering and virtualization;
- project timeline and evidence graph traversal;
- requirement/session/repository filters;
- multi-attempt and controlled-run comparison;
- reviewer queues and annotations; and
- exportable retrospectives.

Python remains authoritative for ingestion, policy, metrics, and persistence. React consumes versioned JSON contracts.

## Milestone 7 — optional LLM judgment providers

This is deliberately later than capture, submission operations, and calibration.

- provider protocol and capability probe;
- remote Claude/Codex judge adapters;
- fresh, isolated, tool-free evaluation sessions;
- evidence-citation validation;
- model/provider/run provenance;
- cross-provider disagreement reporting; and
- optional on-device provider adapters after the remote contract stabilizes.

Local LLM execution is not implemented in the current milestone; only extension schemas and bounded bundles are retained.

## Explicit non-goals before 1.0

- employee or participant ranking;
- hiring, termination, compensation, or promotion decisions;
- covert monitoring;
- a universal developer score;
- reverse-engineering a proprietary competitor’s rubric;
- raw transcript upload by default; and
- silently invoking an LLM during participant submission.
