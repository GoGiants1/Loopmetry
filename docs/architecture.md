# Architecture

## Objective

Loopmetry evaluates the evidence produced while humans and coding agents work on a software project. The system is organized so that source-specific parsing, factual measurements, metric interpretation, and optional prose generation remain separate.

## Data flow

```text
Agent-specific adapters
  Claude Code / Codex / Cursor / OpenCode / Gemini / others
                         │
                         ▼
Canonical event schema (JSONL)
  requirement / plan / file_change / verification / error / commit / ...
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
      In-memory analysis         Local SQLite store
            │                         │
            └────────────┬────────────┘
                         ▼
Deterministic project evaluator
  metric components + evidence refs + confidence + gaps
                         │
                         ▼
Markdown / JSON report
                         │
                         ▼
Optional future narrative layer
  local model or explicit BYOK; never required for metric calculation
```

## Boundaries

### Adapter layer

An adapter converts a tool's transcript or event log into the canonical schema. It owns source-specific concerns such as:

- identifying genuine human turns;
- normalizing tool-call names;
- separating file reads from file changes;
- detecting test, lint, build, and security checks;
- correlating failures and recoveries;
- extracting Git metadata; and
- redacting or excluding raw content.

The evaluator must not contain assumptions about one coding-agent vendor's storage format.

### Canonical evidence layer

The canonical event schema is intentionally small and append-only. Each event has stable project and session IDs, an aware timestamp, an actor, a source, and type-specific data.

The schema stores normalized evidence rather than requiring raw prompts or source code. Adapters may retain source-specific references in their own local index, but those references should not be needed to calculate the core metrics.

### Evidence store

The first implementation uses SQLite because it is local, inspectable, transactional, and available in the Python standard library. The `event_id` primary key makes ingestion idempotent.

The store currently supports:

- transactional event ingestion;
- duplicate suppression;
- ordered project event retrieval; and
- project listing.

Future versions may add schema migrations, content-addressed artifacts, retention controls, and adapter checkpoints.

### Evaluation layer

The evaluator:

1. accepts events for exactly one project;
2. orders them by timestamp and event ID;
3. computes metric components from observable evidence;
4. records the event IDs that support each result;
5. computes confidence from evidence availability; and
6. emits explicit measurement gaps.

It does not call an LLM and does not generate a single overall rank.

### Reporting layer

Reports are currently rendered as Markdown or JSON. JSON is the stable integration surface; Markdown is intended for human review, pull-request artifacts, and project retrospectives.

## Threat model

The most sensitive inputs are likely to be agent transcripts, local paths, repository metadata, customer requirements, and unreleased implementation details. The current core reduces exposure by accepting normalized events and making no network calls.

Adapters should follow these rules:

- prefer allowlisted fields over regex-only redaction;
- avoid storing prompt or source-code excerpts by default;
- omit Git author email unless explicitly required;
- make network transfer opt-in and inspectable;
- give users control over retention and deletion; and
- mark incomplete source coverage in adapter provenance.

See `PRIVACY.md` and `SECURITY.md`.

## Extension points

Planned extension points include:

- an `Adapter` protocol for source parsers;
- a Git evidence enricher;
- specification and requirement graph importers;
- project-volume calibration;
- benchmark task manifests;
- human-evaluation annotations; and
- optional narrative providers.
