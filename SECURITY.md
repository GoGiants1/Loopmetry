# Security policy

Loopmetry handles development evidence, enrollment credentials, and review metadata. Privacy-minimized data can still reveal confidential project information.

## Supported versions

The project is pre-1.0. Security fixes target the latest release and `main`.

## Reporting a vulnerability

Use GitHub private vulnerability reporting when enabled. Do not place real transcripts, participant tokens, administrator tokens, customer data, or proprietary source in a public issue.

## Participant controls

- Keep `.loopmetry/` out of version control.
- Use only authorized repositories and assignments.
- Treat enrollment tokens as passwords.
- Prefer the token environment variable over command-line arguments or URLs.
- The CLI refuses remote plaintext HTTP uploads; use HTTPS.
- Inspect `submission.json` before first deployment or policy change.
- Delete local runs according to the organization’s retention policy.

## Capture requirements

Capture adapters must:

- parse provider payloads as data and never execute transcript content;
- omit raw prompt, source, patch, command, output, secret, and identity bodies;
- convert paths to project-relative or redacted forms;
- write private append-only files where supported;
- enforce per-hook byte limits;
- record source coverage and fail closed on ambiguous sensitive input; and
- remain idempotent.

## Submission protocol

The v1 envelope is content-addressed. Client and server validation must preserve:

- canonical JSON digest verification;
- schema and metric-range checks;
- token-bound assignment/submitter identity;
- request body limits;
- idempotent duplicate handling; and
- no raw event upload.

A bearer token is valid for one active roster identity. Rotating it invalidates the previous token.

## Administrator service

- Bind to `127.0.0.1` by default.
- Use an HTTPS reverse proxy and explicit network controls for remote participants.
- Keep administrator routes private from public upload routes at the proxy.
- Set a long random `LOOPMETRY_ADMIN_TOKEN`.
- Protect the SQLite database, credentials CSVs, exports, and backups.
- Apply request-rate and body-size limits at the reverse proxy.
- Review server logs to ensure credentials and request bodies are never emitted.
- Maintain retention, deletion, and incident-response procedures.

The built-in dashboard uses Basic authentication, HMAC-based CSRF protection for review-state changes, HTML escaping, restrictive content-security policy, no-store caching, frame denial, and no external assets.

## Review and UI requirements

Display names, evidence summaries, reviewer notes, and future model output are untrusted text. Render them escaped; never execute embedded HTML, Markdown extensions, or JavaScript.

Review status must be manual and separate from metric scores. The UI must not default-sort participants by score.

## Future LLM provider requirements

No provider runtime is currently implemented. A future integration must use a fresh isolated context, disable tools and writable repository access, bound payload/time/output, validate every evidence citation, expose remote versus on-device inference, and store judgments separately from deterministic results.
