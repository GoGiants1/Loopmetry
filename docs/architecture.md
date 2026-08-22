# Architecture

## Objective

Loopmetry evaluates evidence produced while humans and coding agents work on a software project, packages a privacy-minimized project submission, and lets an administrator manage a roster and repeated attempts.

Source-specific parsing, normalized evidence, deterministic measurement, submission transport, administrator review, optional LLM interpretation, and presentation remain separate.

## Data flow

```text
Claude Code / Codex hooks or explicit normalized JSONL
                         │
                         ▼
Canonical event schema
  requirement / plan / file_change / verification / error / commit / ...
                         │
               ┌─────────┴─────────┐
               ▼                   ▼
        In-memory analysis   Local evidence SQLite
               │
               ▼
Deterministic ProjectEvaluator
  metric components + evidence IDs + confidence + gaps
               │
       ┌───────┼─────────────────┐
       ▼       ▼                 ▼
 report.json  report.html   submission envelope v1
                                   │
                          explicit authenticated upload
                                   │
                                   ▼
                     Administrator collection service
                       roster / attempts / review status
                                   │
                        ┌──────────┼──────────┐
                        ▼          ▼          ▼
                  HTML dashboard JSON API  CSV export

Optional future branch:
canonical evidence → bounded LLM bundle → provider adapter → separate judgment
```

## Participant-side boundaries

### Capture and adapter layer

An adapter converts a provider hook payload or explicitly selected transcript into canonical events. It owns provider-specific concerns such as genuine human turns, tool names, test/build detection, error recovery, Git metadata, and content minimization.

The evaluator never imports a Claude Code or Codex storage format directly. Current automatic discovery is intentionally restricted to Loopmetry-created files under `.loopmetry/hooks/` and `.loopmetry/events/`.

### Canonical evidence layer

The event schema is append-only and local. Each event has a stable event, project, and session ID; aware timestamp; actor; source; type; and allowlisted data.

Raw prompts, source-code bodies, complete command/output text, secrets, and unnecessary identity fields are not required by core metrics.

### Deterministic evaluation layer

The evaluator:

1. accepts events for exactly one project;
2. orders them by timestamp and event ID;
3. computes metric components from observable evidence;
4. records supporting event IDs;
5. computes confidence from evidence availability; and
6. emits explicit measurement gaps.

It makes no model call and does not generate a universal developer or participant score.

### One-command workflow

`loopmetry run` orchestrates discovery or explicit input, duplicate resolution, project selection, deterministic evaluation, local report generation, submission packaging, optional upload, and receipt persistence.

Each run receives its own private directory. A failed upload never deletes the generated report or submission package.

### Submission layer

The v1 envelope contains the report and compact input provenance, but not the canonical event list. It is content-addressed so an identical retry is idempotent.

Participant identity is bound to a server-issued enrollment token. The server ignores self-asserted identity unless it matches the token’s roster record.

## Administrator-side boundaries

### Enrollment store

The administrator SQLite database contains:

- roster identity and display name;
- active token digest and non-secret hint;
- submission attempts;
- current manual review state and reviewer note; and
- append-only status history.

Plain participant tokens are returned only at enrollment and are never stored.

### Collection API

`POST /api/v1/submissions` authenticates a bearer enrollment token, enforces a body-size limit, validates the content hash and schema, checks token-bound identity, and inserts the submission transactionally.

The same envelope returns its existing receipt. A different envelope for the same participant becomes the next attempt.

### Dashboard and management API

The server-rendered dashboard lists the complete roster, including non-submitters. It provides latest-attempt status, history, metric confidence, measurement gaps, manual state changes, and CSV export.

Review status is operational metadata and is not inferred from a metric score. The default ordering is roster identity, not performance.

### Deployment boundary

The standard-library server binds to loopback by default. Remote use requires an HTTPS reverse proxy and access control. Administrator pages use Basic authentication and CSRF-protected state-change forms. Participant tokens are rejected over remote plaintext HTTP by the CLI.

## Reporting and visualization

JSON is the stable integration contract. Markdown and standalone HTML are local artifacts. The administrator dashboard is server-rendered HTML over SQLite and JSON mappings.

A future React client may consume versioned APIs for evidence-graph traversal, large cohort filtering, comparison, and richer review workflows. It must not duplicate metric formulas.

## LLM extension point

LLM evaluation is not part of the current execution path. The bounded bundle, rubric, and output schema remain as extension contracts. A future provider implementation must store judgments separately from deterministic results, cite submitted evidence IDs, expose inference location, and never modify the evaluated repository.

Local-model runtime integration is explicitly deferred.

## Threat model

Sensitive material can exist in transcripts, local paths, requirements, repository metadata, and generated summaries. Controls include:

- allowlist-based capture;
- no raw event upload in submission v1;
- content-addressed validation;
- token-bound roster identity;
- private local artifact permissions;
- body-size limits;
- HTML escaping and restrictive response headers;
- no network call unless a participant supplies `--server`; and
- explicit retention responsibility for the administrator database.

See `PRIVACY.md`, `SECURITY.md`, and `docs/submission-workflow.md`.
