# Architecture

## Objective

Loopmetry evaluates evidence produced while humans and coding agents work on a software project, packages a privacy-minimized project submission, and lets an administrator manage a roster and repeated attempts.

Source-specific parsing, normalized evidence, deterministic measurement, submission transport, administrator review, optional LLM interpretation, and presentation remain separate.

## Data flow

```text
                Claude Code / Codex
                         │
        ┌────────────────┴────────────────┐
        ▼                                 ▼
 Live hook capture              Historical backfill
 prospective, minimized         consented, local, bounded
 at capture time                discovery and parsing
        │                                 │
        └───────┬─────────────────────────┘
                │        explicit normalized JSONL ──┐
                ▼                                    │
Canonical event schema  ◄────────────────────────────┘
  requirement / plan / file_change / verification / error / commit / ...
  + per-event source, capture-mode, and coverage provenance
                         │
                         ▼
        merge / deduplicate / conflict and gap diagnostics
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

Two source paths are first-class (decision D-011) and both produce the same canonical events behind one shared `SourceAdapter` contract:

- **Live hook capture** collects work prospectively as it happens. It minimizes content at capture time, never sees a full transcript, and is least exposed to provider-internal format changes. It only covers work performed after integration.
- **Historical backfill** recovers sessions that already exist locally. It enables zero-setup analysis and richer plan/conversation context, but depends on versioned vendor formats and requires explicit consent to read local transcripts.

An adapter converts a provider hook payload or an approved historical session into canonical events. It owns provider-specific concerns such as genuine human turns, tool names, test/build detection, error recovery, Git metadata, and content minimization. Neither source path implements metric semantics.

The shared contract requires deterministic discovery ordering, preview before import, incremental checkpoints, source and adapter version recording, per-evidence-category coverage, and unparsed-record diagnostics. Every imported event carries provenance: source, capture mode (`hook`, `history-backfill`, `explicit-import`, `deterministically-derived`), adapter version, a content-minimized source reference, and coverage. Later LLM-derived or human-confirmed information uses separate provenance modes and never silently enters deterministic metrics.

When the same action is observed by both paths, events merge by provider-native event ID when available, otherwise by session, timestamp, event type, and a safe payload fingerprint. A merged event keeps every provenance record. Conflicting observations are never resolved by picking a side; they surface as `adapter_conflict` diagnostics and lower confidence. Neither path outranks the other globally — coverage is compared per evidence category (hooks are strong on exit status; backfill can be richer on planning context).

The evaluator never imports a Claude Code or Codex storage format directly. Current automatic discovery is intentionally restricted to Loopmetry-created files under `.loopmetry/hooks/` and `.loopmetry/events/`.

Historical discovery is bounded and consented: candidates are limited by project root, repository identity, session working directory, and an explicit time window; interactive terminals see a preview (sessions, event counts, data size) and confirm before import; non-interactive runs never read history implicitly. Backfill reads raw transcripts locally in streaming fashion without copying them, stores only canonical events under `.loopmetry/`, converts absolute paths to repository-relative paths or hashes, counts rather than drops unknown records, records source-CLI and adapter versions, uses incremental checkpoints, detects transcript rotation, and caps pathological inputs with timeouts that degrade coverage to `partial` instead of aborting. Real user transcripts are never committed as test fixtures.

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

A planned `--source auto` mode extends this flow: hook events and explicit files are used unconditionally, historical session candidates are previewed and imported only with consent (`--include-history` in non-interactive runs), and the merged evidence flows through the same deterministic pipeline. `--source hook` and `--source history` select a single path explicitly.

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
