# Participant submission and administrator collection

## Product boundary

This milestone optimizes two operational paths:

1. a participant executes one command at the end of project work; and
2. an administrator sees the full roster, collects retries idempotently, and assigns manual review status.

The submission service does not receive raw agent transcripts or canonical event logs. Local LLM execution is not implemented in this milestone.

## End-to-end flow

```text
Claude Code / Codex lifecycle hooks
            │
            ▼
<project>/.loopmetry/hooks/*.jsonl
            │
            ▼
loopmetry run
  discover → merge → de-duplicate → select project
            │
            ▼
Deterministic ProjectEvaluator
            │
     ┌──────┴───────────┐
     ▼                  ▼
report.json        standalone report.html
     │
     ▼
submission.json
  report + counts + privacy declarations
  no raw events / prompts / source bodies
     │
     ▼
POST /api/v1/submissions
  Bearer enrollment token
     │
     ▼
Administrator SQLite store
  roster + attempts + status history
     │
     ▼
HTML dashboard / JSON API / CSV export
```

## Participant command contract

The administrator-generated credentials CSV contains a copy/paste POSIX command and a PowerShell command. Both use `uvx --from git+https://github.com/GoGiants1/Loopmetry.git`, so a participant only needs `uv` plus the preconfigured capture events; a separate Loopmetry installation is not required.

The `run` command accepts repeated `--input` values or discovers the following directories under `--root`:

```text
.loopmetry/hooks/*.jsonl
.loopmetry/events/*.jsonl
```

Discovery is deliberately narrow. It does not scan a user’s home directory or guess unstable vendor transcript formats. Events backfilled by `loopmetry history import --source claude-code` (decision D-011) land under `.loopmetry/events/` and are picked up by this same discovery step exactly like hook-captured events. Default `loopmetry run` (no `--source` flag) never reads local history itself, only the already-consented, already-written output of a prior `history import`. `loopmetry run --source auto` is the exception: it actively triggers a consented Claude Code history scan as part of the same command — interactively via the same double-confirmation prompts as `history import`, or non-interactively only when `--include-history` is passed — and merges the result with hook/explicit evidence before evaluation (decision D-015).

The run directory contains:

```text
.loopmetry/runs/<run-id>/
  manifest.json
  report.json
  report.html
  submission.json
  receipt.json        # only after successful upload
```

Analysis and submission packaging complete before upload. If the network request fails, `submission.json` remains available for the `loopmetry submit` retry command.

## Enrollment and identity

Each participant is enrolled under the composite roster key:

```text
assignment_id + submitter_id
```

Enrollment creates a cryptographically random bearer token. The administrator database stores only its SHA-256 digest and a short non-secret hint. A token authenticates exactly one assignment/submitter pair; the server rejects an envelope that claims another identity.

Token rotation immediately invalidates the previous token.

## Submission envelope v1

`schemas/submission-v1.schema.json` defines the external contract. The envelope is content-addressed by canonical JSON serialization of every field except `submission_id` itself.

Required sections:

- assignment, submitter, project, run, and client identifiers;
- client creation timestamp;
- input source/event/session counts and observed window;
- deterministic report;
- privacy declarations.

Privacy invariants:

```json
{
  "raw_transcripts_included": false,
  "raw_source_code_included": false,
  "canonical_events_included": false,
  "absolute_source_paths_included": false
}
```

The server recomputes the digest, validates metric ranges, checks the report/project relationship, and enforces the enrollment identity before persistence.

## Attempts and idempotency

The first accepted envelope for one roster identity becomes attempt 1. A different valid envelope becomes attempt 2, and so on.

Re-uploading an identical `submission.json` returns the original receipt with `duplicate: true` and does not create another attempt. This supports safe retries after uncertain network outcomes.

## Review state

Review state is manual and separate from metric values:

- `received`
- `reviewing`
- `needs_revision`
- `accepted`

`not_submitted` is a dashboard state derived from the roster; it is not stored as a submission status.

Status changes are append-only in `status_history`, while the latest value is denormalized on the submission row for fast cohort views. The dashboard never sorts participants by a metric score by default.

## Administrator HTTP surface

Participant endpoint:

```text
POST /api/v1/submissions
Authorization: Bearer <participant token>
Content-Type: application/json
```

Administrator surfaces use HTTP Basic authentication with username `admin` and the configured administrator token:

```text
GET  /
GET  /submission/<submission-id>
POST /submission/<submission-id>/status
GET  /api/v1/participants
GET  /api/v1/submissions
GET  /api/v1/submissions/<submission-id>
GET  /export.csv
GET  /healthz                       # no authentication
```

Status forms include an HMAC-based CSRF token. HTML and JSON responses set no-store, frame, content-type, referrer, and content-security headers.

## Deployment boundary

The built-in server is intentionally small and uses Python’s standard library. It binds to `127.0.0.1` by default.

For network deployment:

1. keep the Loopmetry process on a private loopback or internal interface;
2. terminate TLS at a reverse proxy;
3. restrict administrator routes separately from participant upload routes;
4. back up and apply retention policy to the SQLite database;
5. rotate administrator and participant credentials when exposure is suspected; and
6. monitor body-size and request-rate limits at the proxy.

The participant client refuses to transmit a bearer token over plaintext HTTP to a non-loopback host.

## Future extensions

The v1 boundaries permit later addition of:

- per-assignment deadlines and late flags;
- signed administrator receipts;
- object storage for optional artifacts;
- SSO and role-based review access;
- a React client over the existing JSON APIs;
- server-side rubric versions; and
- optional LLM judge results stored as a separate, explicitly versioned artifact.

None of those extensions should require raw transcript upload or changing deterministic metric semantics.
