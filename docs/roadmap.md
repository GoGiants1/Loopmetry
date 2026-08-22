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

## Milestone 2 — source capture and backfill

- reviewed hook-configuration generator for Claude Code;
- reviewed hook-configuration generator for Codex;
- Claude Code historical-session adapter;
- Codex rollout backfill adapter;
- adapter checkpoints and incremental import;
- source/adapter version and coverage matrix; and
- unparsed-record diagnostics.

Hooks remain the preferred live path. Home-directory transcript scanning must always be explicit and bounded.

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
