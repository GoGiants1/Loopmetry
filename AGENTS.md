# AGENTS.md

## Project mission

Loopmetry is a local-first, evidence-backed evaluator for human–agent software work. It reconstructs how project intent became plans, file changes, verification evidence, recovery actions, and delivered results.

The product evaluates the **project workflow**, not the person. Do not introduce employee ranking, hiring signals, covert monitoring, or a universal developer score.

## Repository map

- `src/loopmetry/schema.py`: canonical event model and validation
- `src/loopmetry/io.py`: JSONL loading and project selection
- `src/loopmetry/storage.py`: local SQLite evidence store
- `src/loopmetry/evaluation.py`: evaluation orchestration
- `src/loopmetry/metrics_*.py`: deterministic metric implementations
- `src/loopmetry/report.py`: JSON, Markdown, and self-contained HTML rendering
- `src/loopmetry/cli.py`: command-line interface
- `docs/event-schema.md`: canonical event contract
- `docs/metrics.md`: metric cards, formulas, limitations, and calibration needs
- `docs/architecture.md`: architectural boundaries and data flow
- `docs/llm-evaluation.md`: design for optional local-CLI LLM judges
- `schemas/`: stable machine-readable contracts
- `examples/`: synthetic, non-sensitive fixtures
- `tests/`: unit and CLI tests

## Environment and commands

Use `uv` for Python and dependency management. Do not add ad-hoc virtualenv, pip-tools, Poetry, or requirements files.

```bash
uv sync --all-groups
uv run python -m unittest discover -s tests -v
uv run loopmetry validate examples/demo_project.jsonl
uv run loopmetry analyze examples/demo_project.jsonl --format json
uv build
```

When dependencies or supported Python versions change, update `pyproject.toml`, regenerate `uv.lock`, and run `uv lock --check`.

The runtime should remain Python-standard-library-only unless a dependency has a clear portability, security, and maintenance justification. Development-only dependencies belong in the `dev` dependency group.

## Architectural invariants

1. Keep source parsing, normalized evidence, deterministic measurements, LLM interpretation, and report rendering separate.
2. Vendor-specific transcript assumptions belong in adapters, never in the core evaluator.
3. Every metric or LLM claim must point to canonical evidence IDs.
4. Missing evidence must lower confidence or appear as an explicit gap; never silently convert absence into a positive claim.
5. Do not collapse independent metrics into a hidden overall rank.
6. JSON is the stable integration surface. Markdown and HTML are human-facing renderings; neither may contain logic that changes evaluation results.
7. Preserve deterministic behavior: the same canonical events must produce the same deterministic metric results.

## Canonical event changes

Before adding or changing an event type:

- explain why existing types cannot represent the evidence;
- document required and optional fields;
- define privacy implications and retention needs;
- identify which adapters can emit it reliably;
- preserve backwards compatibility or add an explicit migration; and
- add schema, parser, storage, and adversarial tests.

Do not store raw prompts, source-code bodies, credentials, Git author email, or remote repository URLs when normalized evidence is sufficient.

## Metric changes

A metric change must include:

- a plain-language construct definition;
- an exact deterministic formula and weights;
- required evidence types and confidence behavior;
- known confounders, false positives, and false negatives;
- counterexamples and missing-evidence tests;
- sensitivity or calibration notes; and
- a statement of unsupported uses.

Avoid false precision. Prefer inspectable components over one opaque score.

## Adapter rules

Adapters should:

- accept an explicit transcript path or official hook payload when possible;
- distinguish genuine user turns, agent messages, tool calls, tool results, and subagents;
- normalize test, lint, build, security, edit, and Git evidence without copying unnecessary content;
- record source version, parser version, coverage, warnings, and source references;
- be idempotent; and
- fail closed on malformed or ambiguous sensitive data.

Directory scanning is a compatibility fallback. Prefer lifecycle hooks that provide `session_id`, `transcript_path`, and `cwd`.

## Optional LLM evaluation

The deterministic evaluator remains authoritative for factual measurements. LLM evaluation is a separate, optional interpretation layer.

- Build a bounded, allowlisted evaluation bundle from canonical evidence.
- Do not send the raw transcript by default.
- Show or save the exact outbound payload before invoking a network-backed provider.
- Record three separate facts: where the session data lives, which local CLI transports the request, and whether inference is remote or on-device. A local CLI is not automatically a local model.
- Codex OSS mode may use an on-device Ollama or LM Studio backend; probe and record that backend explicitly instead of inferring it from the executable name.
- Run judges in fresh, ephemeral, read-only sessions that do not inherit project agent instructions, plugins, hooks, or MCP servers.
- Require JSON Schema-conformant output, evidence IDs, counterevidence, confidence, missing evidence, and human-review flags.
- Record provider, model, CLI version, rubric version, prompt hash, input hash, output hash, token usage, cost when available, and exit status.
- Never let the judge modify the evaluated repository.
- Keep same-model self-evaluation visible in metadata and support cross-provider judging.

## Privacy and safety

- Network use must be explicit, opt-in, and documented.
- Use allowlists rather than regex-only redaction.
- Keep `.loopmetry/`, generated reports, evaluator bundles, and local session material out of Git.
- Never commit real user transcripts, customer data, secrets, private paths, or proprietary source excerpts.
- Synthetic fixtures must not resemble real confidential projects.
- Preserve the restrictions in `PRIVACY.md`, `SECURITY.md`, and `RESPONSIBLE_USE.md`.

## Testing and definition of done

For every behavior change:

1. Add or update tests, including missing and adversarial evidence.
2. Run `uv lock --check`.
3. Run `uv run python -m unittest discover -s tests -v`.
4. Validate and render the demo fixture in JSON and HTML.
5. Run `uv build` for packaging changes.
6. Update relevant docs and schemas.

A change is not complete if its output cannot be traced to evidence, its privacy behavior is unclear, or its failure mode is undocumented.

## Change discipline

Keep commits focused. Do not mix broad refactors with metric or schema behavior changes. Avoid unrelated formatting churn. Preserve public CLI and JSON compatibility unless the change is explicitly versioned and documented.
