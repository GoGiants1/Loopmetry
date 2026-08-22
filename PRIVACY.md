# Privacy

## Participant-side data flow

Loopmetry capture, normalization, deterministic evaluation, local SQLite storage, and report rendering run on the participant’s machine without telemetry.

`loopmetry run` writes local artifacts under `.loopmetry/runs/<run-id>/`. A network request occurs only when the participant configures `--server` or `LOOPMETRY_SERVER_URL`.

## Submission v1 data

The administrator receives a content-addressed envelope containing:

- assignment, roster, project, run, and Loopmetry version identifiers;
- event/session/source counts and the observed time window;
- deterministic project report;
- evidence IDs and privacy-minimized evidence summaries;
- metric confidence and measurement gaps; and
- explicit privacy declarations.

Submission v1 does not contain:

- raw Claude Code or Codex transcripts;
- raw prompts or full model responses;
- canonical event records;
- source-code bodies or complete diffs;
- raw environment variables or credentials;
- Git author email or remote repository URL; or
- absolute source path prefixes.

Evidence summaries can still contain confidential project terms. Capture adapters therefore use allowlisted fields and bounded summaries, and organizations should use only authorized training or project data.

## Identity and roster data

The administrator database may contain:

- assignment and submitter identifiers;
- optional display name;
- token digest and non-secret hint;
- submission attempts and timestamps;
- manual review status and reviewer notes; and
- validated submission envelopes.

Plain participant tokens are returned only during enrollment and are not stored. Credentials exports contain sensitive bearer tokens and are written with private permissions; administrators must distribute and delete them securely.

## Administrator responsibilities

The operator of the collection service is responsible for:

- providing participant notice;
- establishing an appropriate data-processing basis;
- limiting access to roster, submission, and review data;
- configuring HTTPS for network submission;
- defining retention, export, correction, and deletion procedures;
- protecting backups and CSV exports;
- rotating credentials after suspected exposure; and
- avoiding employment or covert-monitoring uses prohibited by `RESPONSIBLE_USE.md`.

## Local files

`.loopmetry/`, administrator databases, generated reports, receipts, and credentials CSVs are ignored by the repository. Users should also apply OS-level access controls and avoid syncing sensitive artifacts to unmanaged storage.

## LLM extension

No model is invoked during capture, `run`, upload, ingestion, or administrator review in the current milestone.

The bounded LLM bundle remains a future extension contract. Any later provider must make the exact outbound payload, provider, model, inference location, retention, and cost explicit. Local/on-device LLM runtime support is deferred.
