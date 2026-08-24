# AGENTS.md

## Purpose and current boundary

Loopmetry is a local-first, evidence-backed evaluator and submission system for human–agent software work. It evaluates the **project workflow**, not the person.

Do not introduce participant ranking, employment signals, covert monitoring, or a universal developer score.

The current milestone is dual-source capture: prospective hook capture and retrospective historical backfill behind one shared adapter contract, converging on a hybrid `loopmetry run --source auto` participant flow. The one-command participant flow and administrator cohort collection are implemented. Local/on-device LLM execution is deferred; preserve the extension boundary without adding a model runtime. See `docs/decision-log.md` entries D-007, D-008, and D-011.

## Start here

Before editing, identify the question you are answering and follow this source-of-truth order:

| Question | Read first |
|---|---|
| Why was a direction chosen? | `docs/decision-log.md` |
| What are the component boundaries? | `docs/architecture.md` |
| What is implemented now? | code and the nearest tests |
| What is planned next? | `docs/roadmap.md` |
| What is the external contract? | `schemas/`, `rubrics/`, and the matching task document |
| What are the safety constraints? | `PRIVACY.md`, `SECURITY.md`, `RESPONSIBLE_USE.md` |

Then route by task:

| Area | Contract and design | Primary implementation | Tests |
|---|---|---|---|
| One-command participant flow | `docs/submission-workflow.md` | `src/loopmetry/cli.py`, `src/loopmetry/workflow.py` | `tests/test_cli.py`, `tests/test_workflow.py` |
| Submission package and upload | `docs/submission-workflow.md`, `schemas/submission-v1.schema.json` | `src/loopmetry/submission.py` | `tests/test_submission.py` |
| Roster, attempts, review status | `docs/submission-workflow.md` | `src/loopmetry/admin_storage.py`, `src/loopmetry/admin_server.py` | `tests/test_admin_storage.py`, `tests/test_admin_server.py` |
| Claude Code / Codex capture | `docs/hook-capture.md`, `docs/event-schema.md` | `src/loopmetry/hook_capture.py`, `src/loopmetry/schema.py` | `tests/test_hook_capture.py`, `tests/test_schema.py` |
| Source adapters and historical backfill | `docs/architecture.md`, `docs/hook-capture.md`, `docs/decision-log.md` D-011, D-013 | `src/loopmetry/adapters/`, `src/loopmetry/adapters/claude_code_history.py`, `src/loopmetry/hook_capture.py`, `src/loopmetry/minimize.py` | `tests/test_adapters.py`, `tests/test_claude_code_history.py`, `tests/test_hook_capture.py` |
| Deterministic evaluation | `docs/metrics.md`, `docs/event-schema.md` | `src/loopmetry/evaluation.py`, `src/loopmetry/metrics_*.py` | `tests/test_evaluation.py` |
| Reports and visualization | `docs/web-ui.md` | `src/loopmetry/report.py` | `tests/test_report.py` |
| Future LLM boundary | `docs/llm-evaluation.md`, `schemas/llm-evaluation-*.json`, `rubrics/` | `src/loopmetry/llm_bundle.py` | `tests/test_llm_bundle.py` |
| Local persistence | `docs/architecture.md` | `src/loopmetry/storage.py`, `src/loopmetry/admin_storage.py` | `tests/test_storage.py`, `tests/test_admin_storage.py` |

Do not use prior chat context as the source of truth when repository documents, schemas, tests, or code are available. The decision log records direction, not implementation status.

## Stable invariants

1. Keep provider capture, canonical evidence, deterministic evaluation, submission transport, administrator review, optional LLM interpretation, and rendering separate.
2. The same canonical event set must produce the same deterministic result.
3. Every metric claim must be traceable to canonical evidence IDs.
4. Missing evidence lowers confidence or becomes an explicit gap; it is never silently treated as success.
5. Submission payloads exclude raw transcripts, source bodies, secrets, absolute private paths, and unnecessary identity data.
6. Identical submission retries are idempotent; a new analysis run becomes a new attempt.
7. Review status is manual operational metadata and must not be inferred from metric values.
8. External CLI, JSON, and JSON Schema contracts are versioned. Renderers must not recompute evaluation or review state.
9. Local/on-device LLM execution is deferred. Do not invoke a model in the participant or administrator path.
10. Prospective hook capture and retrospective historical backfill are both first-class source paths behind one adapter contract. Imported events carry source, capture-mode, adapter-version, and coverage provenance; overlapping observations merge without losing provenance; conflicts stay visible and lower confidence; history discovery outside the project requires bounded scope, preview, and explicit consent, and non-interactive runs never read history implicitly.

## Environment and verification

Use `uv` for Python versions, dependency locking, commands, and builds.

```bash
uv sync --locked
uv lock --check
uv run python -m unittest discover -s tests -v
uvx --from ruff==0.12.12 ruff check .
uv run loopmetry run --input examples/demo_project.jsonl \
  --assignment-id demo --submitter-id local
uv build
```

Do not add Poetry, pip-tools, requirements files, or a separately managed virtual environment. Keep the runtime standard-library-only unless a dependency has a documented portability, security, and maintenance justification.

A change is incomplete when its evidence path, privacy behavior, idempotency, identity boundary, migration, or failure mode is unclear.

## Decision and documentation discipline

`AGENTS.md` contains stable operating rules and navigation only. Keep rationale and historical choices in `docs/decision-log.md`; keep implementation detail in the task-specific document.

When making or changing an enduring product, architecture, security, data-contract, or workflow decision:

1. add a dated entry to `docs/decision-log.md` with status, context, decision, consequences, and related paths;
2. update the affected task document, schema, and tests;
3. mark an earlier decision as **Superseded** rather than silently rewriting history; and
4. keep transient debugging notes and incident details out of the decision log.

Keep commits focused and avoid unrelated formatting churn.
