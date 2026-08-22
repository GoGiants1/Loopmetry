# Contributing

## Development setup

```bash
git clone https://github.com/GoGiants1/Loopmetry.git
cd Loopmetry
uv sync --locked
uv run python -m unittest discover -s tests -v
uvx --from ruff==0.12.12 ruff check .
```

Use `uv` for Python versions, environments, locking, commands, and builds. Runtime code should remain standard-library-only unless a dependency has a clear security, maintenance, and portability justification.

## Design requirements

Contributions to metrics or adapters should follow these rules:

1. Separate source parsing from metric computation.
2. Preserve an evidence path from every interpretation to canonical event IDs.
3. Emit confidence and measurement gaps.
4. Do not add a hidden or universal developer score.
5. Add adversarial and missing-evidence tests.
6. Document false positives, false negatives, and unsupported source behavior.
7. Avoid raw prompt or source-code storage when normalized evidence is sufficient.

## Adding an event type

Before expanding the canonical schema, document:

- why existing types cannot represent the evidence;
- required and optional data fields;
- privacy implications;
- which adapters can emit it reliably; and
- how old persisted data will be migrated.

## Adding a metric

A metric proposal must include:

- a plain-language construct definition;
- exact deterministic formula and weights;
- required evidence types;
- confidence calculation;
- known confounders;
- counterexamples;
- tests for missing and adversarial evidence; and
- a statement of unsupported uses.

## Pull requests

Keep changes focused and include tests. For user-visible changes, update the relevant document in `docs/` and the example report workflow when applicable.


## Dependency and environment changes

Update `pyproject.toml`, run `uv lock`, and commit `uv.lock`. Verify with:

```bash
uv lock --check
uv sync --locked
```

Do not add `requirements.txt`, Poetry, pip-tools, or a separately managed virtual environment. One-shot developer tools should use the repository-pinned `uvx --from package==version` command documented in `AGENTS.md`.

## Coding-agent instructions

`AGENTS.md` is the shared source of repository instructions. `CLAUDE.md` imports it so Codex and Claude Code receive the same rules. Keep the shared instructions concrete, current, and consistent with the architecture and responsible-use documents.

## Submission and administrator changes

Changes to `submission.py`, `admin_storage.py`, `admin_server.py`, or `workflow.py` must preserve:

- content-addressed and idempotent submissions;
- enrollment-token identity binding;
- no raw event/transcript upload;
- complete-roster visibility, including non-submitters;
- attempt and status history;
- manual review state independent of scores;
- loopback-first server behavior and remote HTTPS requirements; and
- tests for duplicate upload, token rotation, CSRF, and malformed payloads.

Update `schemas/submission-v1.schema.json` and `docs/submission-workflow.md` whenever the external envelope changes. Use a new schema version for breaking changes.
