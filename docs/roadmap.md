# Roadmap

## Milestone 0 — evidence evaluator vertical slice

Status: implemented in the initial repository scaffold.

- canonical event schema;
- JSONL validation;
- SQLite evidence store;
- deterministic metric cards;
- confidence and measurement gaps;
- Markdown, JSON, and self-contained HTML reports;
- demo events and unit tests; and
- `uv`-managed development and CI environment.

## Milestone 1 — local session adapters and capture

Priority order:

1. Claude Code hook payload and JSONL adapter;
2. Codex hook payload and interactive-session rollout adapter;
3. Cursor event/history adapter;
4. OpenCode adapter; and
5. generic OpenTelemetry-style ingestion.

The preferred integration is an official lifecycle hook that supplies `session_id`, `transcript_path`, and `cwd`. Directory scanning is a fallback for existing sessions.

Each adapter must publish a coverage matrix showing which canonical events can be emitted reliably, plus parser version, source version, warnings, and unparsed event counts.

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

## Milestone 4 — optional local-CLI LLM judges

- bounded and content-addressed evaluation bundles;
- outbound payload preview and allowlist-based redaction;
- versioned rubric and JSON Schema contracts;
- Codex CLI provider using ephemeral, read-only structured-output runs;
- Claude Code CLI provider using bare, non-persistent, tool-free structured-output runs;
- provider probing, typed failures, usage and cost metadata;
- separation of factual metrics from LLM judgments; and
- cross-provider and repeated-judge disagreement reporting.

See `docs/llm-evaluation.md`.

## Milestone 5 — evaluation and calibration framework

- benchmark project manifests;
- expected evidence assertions;
- regression fixtures for adapters, metrics, bundles, and providers;
- synthetic adversarial traces and prompt-injection fixtures;
- project-level ground-truth annotations;
- human-evaluation forms;
- inter-rater agreement and judge calibration reports; and
- threshold and weight sensitivity analysis.

## Milestone 6 — local React dashboard

The static HTML report is already implemented. The React milestone adds:

- project timeline;
- evidence graph viewer;
- metric and LLM-judgment drill-down;
- session and requirement filters;
- outbound payload preview;
- exportable retrospective; and
- no-network default.

## Explicit non-goals before 1.0

- employee ranking;
- hiring or termination decisions;
- covert monitoring;
- a universal developer score;
- reverse-engineering a proprietary competitor's scoring algorithm;
- sending raw transcripts to a hosted service by default; and
- describing a locally installed provider CLI as on-device inference.
