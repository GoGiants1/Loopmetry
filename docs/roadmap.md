# Roadmap

## Milestone 0 — evidence evaluator vertical slice

Status: implemented in the initial repository scaffold.

- canonical event schema;
- JSONL validation;
- SQLite evidence store;
- deterministic metric cards;
- confidence and measurement gaps;
- Markdown and JSON reports;
- demo events and unit tests.

## Milestone 1 — source adapters

Priority order:

1. Claude Code JSONL adapter;
2. Codex interactive-session adapter;
3. Cursor event/history adapter;
4. OpenCode adapter; and
5. generic OpenTelemetry-style ingestion.

Each adapter should publish a coverage matrix showing which canonical events can be emitted reliably.

## Milestone 2 — project evidence graph

- requirement and acceptance-criterion import;
- Spec → Task → File → Test → Commit edges;
- Git history correlation;
- issue and pull-request correlation;
- evidence graph export; and
- requirement-level completion reports.

## Milestone 3 — project-volume calibration

Raw counts are not comparable across a small bug fix and a multi-week application. Planned work:

- project size descriptors;
- changed-file and language normalization;
- session-duration and active-time estimates;
- task-complexity buckets;
- repository coverage reporting; and
- metric confidence adjusted by observed volume.

## Milestone 4 — evaluation framework

- benchmark project manifests;
- expected evidence assertions;
- regression fixtures for adapters and metrics;
- synthetic adversarial traces;
- project-level ground-truth annotations;
- human-evaluation forms; and
- agreement and calibration reports.

## Milestone 5 — local dashboard

- project timeline;
- evidence graph viewer;
- metric drill-down;
- session and requirement filters;
- exportable retrospective; and
- no-network default.

## Explicit non-goals before 1.0

- employee ranking;
- hiring or termination decisions;
- covert monitoring;
- a universal developer score;
- reverse-engineering a proprietary competitor's scoring algorithm; and
- sending raw transcripts to a hosted service by default.
