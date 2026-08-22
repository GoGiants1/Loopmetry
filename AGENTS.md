# AGENTS.md

## Mission and current priority

Loopmetry is a local-first, evidence-backed evaluator and submission system for human–agent software work. It reconstructs how project intent became plans, changes, verification, recovery, and delivery.

The current product priority is:

1. one participant command for analysis, report generation, and optional submission;
2. administrator roster, collection, attempt history, and manual review status; and
3. stable extension contracts for future LLM judges.

Local/on-device LLM execution is explicitly deferred. Do not spend the current milestone implementing model runtimes, provider-specific local inference, or automatic semantic judging.

The product evaluates the **project workflow**, not the person. Do not add participant ranking, employment signals, covert monitoring, or a universal score.

## Repository map

- `src/loopmetry/schema.py`: canonical event model and validation
- `src/loopmetry/hook_capture.py`: privacy-minimized Claude Code/Codex hook normalization
- `src/loopmetry/io.py`: canonical JSONL input
- `src/loopmetry/evaluation.py`, `metrics_*.py`: deterministic evaluation
- `src/loopmetry/report.py`: JSON, Markdown, and standalone HTML reports
- `src/loopmetry/workflow.py`: one-command participant orchestration
- `src/loopmetry/submission.py`: submission v1, hashing, validation, upload
- `src/loopmetry/admin_storage.py`: roster, attempts, review status, SQLite
- `src/loopmetry/admin_server.py`: collection API and administrator HTML
- `src/loopmetry/llm_bundle.py`: future LLM extension bundle only
- `src/loopmetry/cli.py`: participant and administrator CLI
- `schemas/submission-v1.schema.json`: external submission contract
- `docs/submission-workflow.md`: operational architecture
- `tests/`: synthetic, non-sensitive tests

## Environment

Use `uv` for Python, dependency locking, tool execution, and builds.

```bash
uv sync --locked
uv run python -m unittest discover -s tests -v
uvx --from ruff==0.12.12 ruff check .
uv run loopmetry run --input examples/demo_project.jsonl \
  --assignment-id demo --submitter-id local
uv build
```

Do not add Poetry, pip-tools, requirements files, or an independently managed virtual environment. Runtime code should remain standard-library-only unless a dependency has a documented portability, maintenance, and security justification.

## Architectural invariants

1. Keep provider capture, canonical evidence, deterministic evaluation, submission transport, administrator review, future LLM interpretation, and rendering separate.
2. The same canonical event set must produce the same deterministic metric result.
3. Every metric claim must point to canonical evidence IDs.
4. Missing evidence lowers confidence or becomes an explicit gap; it is never silently converted into success.
5. Submission v1 contains the report and compact provenance, not raw canonical events or transcripts.
6. Participant identity is bound to an enrollment token and checked server-side.
7. Identical submission retries are idempotent; a new run becomes a new attempt.
8. Review state is manual operational metadata and must not be inferred from scores.
9. JSON and JSON Schema are external contracts. HTML must not recompute metrics or review state.
10. No hidden overall participant/developer rank.

## One-command participant workflow

`loopmetry run` must remain a bounded orchestration command:

```text
discover/explicit input → merge/de-duplicate → evaluate → render → package → optional upload
```

Requirements:

- discovery is limited to Loopmetry-owned project directories;
- explicit `--input` always remains available;
- local reports and `submission.json` are written before upload;
- failed uploads are retryable with `loopmetry submit`;
- tokens are read from named environment variables, not persisted in artifacts;
- remote plaintext HTTP token transmission is refused; and
- generated files use private permissions where supported.

## Administrator and submission changes

Before changing the submission contract or administrator database:

- preserve schema versioning and content-hash semantics;
- document migration and backwards compatibility;
- enforce request-size, identity, and metric-range validation;
- preserve non-submitters in cohort views through the roster table;
- retain attempt and status history;
- keep dashboard ordering independent of metric scores;
- add idempotency, duplicate, token-rotation, and concurrency tests; and
- update `docs/submission-workflow.md`, privacy, and security documentation.

Plain enrollment tokens must never be stored. Credentials exports must use private permissions and must remain ignored by Git.

## Capture and adapter changes

Adapters should:

- prefer official hook payloads or explicit transcript paths;
- distinguish human, agent, tool, and subagent evidence;
- normalize only allowlisted facts;
- avoid prompt, source, patch, command, output, secret, and identity bodies;
- record source/adapter versions, coverage, warnings, and unparsed counts;
- be idempotent; and
- fail closed on malformed sensitive data.

Do not recursively scan a home directory or claim support for an unstable provider format without fixtures and a coverage matrix.

## Metric changes

A metric proposal requires:

- construct definition;
- exact deterministic formula and weights;
- required evidence and confidence behavior;
- confounders and counterexamples;
- missing/adversarial evidence tests;
- calibration notes; and
- unsupported-use statement.

Avoid false precision. Prefer inspectable components over opaque scores.

## Future LLM extension

Only preserve extension boundaries in the current milestone:

- bounded allowlisted bundle;
- versioned rubric and output schema;
- separate provider protocol;
- evidence-citation validation; and
- separate storage from deterministic results.

Do not invoke an LLM during `run`, submission upload, or administrator ingestion. Do not implement local model provider runtime until the remote contract and human calibration are stable.

## Privacy and security

- Network use is explicit and opt-in.
- Raw transcripts, canonical events, source bodies, secrets, absolute paths, and identity metadata are excluded from submission v1.
- Treat evidence summaries, display names, reviewer notes, and future LLM output as untrusted text.
- Escape HTML and keep restrictive headers.
- Keep state-changing administrator forms CSRF-protected.
- Bind the built-in server to loopback by default; remote use requires HTTPS termination and access control.
- Keep `.loopmetry/`, credentials CSVs, reports, receipts, databases, and real session material out of Git.
- Preserve `PRIVACY.md`, `SECURITY.md`, and `RESPONSIBLE_USE.md` restrictions.

## Definition of done

For every behavior change:

1. Add unit and integration tests, including missing/adversarial evidence.
2. Run `uv lock --check`.
3. Run `uv run python -m unittest discover -s tests -v`.
4. Exercise `loopmetry run` and the administrator upload/dashboard path.
5. Run lint and `uv build` for packaging changes.
6. Update affected docs and schemas.

A change is incomplete if its evidence path, privacy behavior, idempotency, identity boundary, migration, or failure mode is unclear.
