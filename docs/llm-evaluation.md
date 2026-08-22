# Optional LLM evaluation extension

## Status

LLM judging is **design-only and deferred** in the current milestone. Participant analysis and administrator collection are entirely deterministic and do not invoke a model.

Loopmetry retains a bounded evaluation-bundle contract, rubric, and result schema so provider adapters can be added later without changing canonical events, deterministic metrics, or submission v1. Local/on-device inference is a later extension after the remote provider and calibration contracts stabilize.

## Architectural rule

```text
canonical evidence
      ├── deterministic evaluator → authoritative factual measurements
      └── bounded bundle → optional future LLM judge → separate judgment
```

An LLM judgment must never overwrite an observed event, modify a deterministic metric, or become a hidden overall participant score.

## Existing extension contracts

- `src/loopmetry/llm_bundle.py`: bounded, content-addressed, normalized-evidence bundle
- `rubrics/project-work-v1.md`: initial semantic rubric
- `schemas/llm-evaluation-bundle-v1.schema.json`: bundle contract
- `schemas/llm-evaluation-v1.schema.json`: structured judgment contract

The current CLI only creates a previewable bundle:

```bash
uv run loopmetry bundle events.jsonl --output bundle.json
```

No provider process is started.

## Future provider interface

A provider implementation should conceptually expose:

```text
probe() -> ProviderCapabilities
evaluate(bundle, rubric, output_schema, options) -> JudgeRun
```

`ProviderCapabilities` records:

- executable or API availability;
- provider and model identity;
- CLI/provider version;
- authentication readiness;
- structured-output support;
- isolation controls;
- inference location: remote or on-device; and
- usage/cost metadata availability.

`JudgeRun` records:

- provider/model/runtime versions;
- bundle, rubric, prompt, and schema hashes;
- start/end time and exit status;
- token usage and cost when available;
- typed diagnostics; and
- validated structured output.

Provider output is stored separately from deterministic reports and participant submission v1. A future submission schema may reference an optional judgment artifact explicitly.

## Bundle boundary

The judge receives bounded normalized evidence, not a raw transcript. Included fields may cover:

- project/session/requirement/evidence IDs;
- normalized plans and change summaries;
- verification and recovery facts;
- bounded Git statistics;
- source coverage and measurement gaps; and
- an outbound-field manifest.

Excluded by default:

- raw prompts and complete model messages;
- source-code bodies and full diffs;
- credentials and environment variables;
- author identity and email;
- remote repository URLs; and
- absolute private path prefixes.

All text derived from a transcript is untrusted data, never an instruction to the judge.

## Result boundary

Each semantic dimension should contain:

- ordinal rating or `null` when unassessable;
- assessability and confidence;
- concise rationale;
- supporting evidence IDs;
- counterevidence IDs;
- missing evidence; and
- a human-review flag.

Every cited ID must exist in the exact submitted bundle. Unknown evidence invalidates the result.

The top-level verdict may be `pass`, `partial`, `fail`, or `indeterminate`, representing project-evidence sufficiency rather than developer ability.

## Provider sequence

Recommended implementation order after deterministic calibration:

1. provider protocol and typed failures;
2. one remote, structured-output provider with payload preview;
3. fresh, isolated, tool-free execution;
4. evidence-ID validation and append-only run storage;
5. cross-provider disagreement reporting;
6. consented human calibration; and only then
7. optional on-device providers such as Ollama or LM Studio.

A locally installed CLI is not automatically local inference. Future UI and audit records must distinguish:

```text
session data location
transport/runtime location
model inference location
```

## Safety and evaluation requirements

Before provider invocation ships, add fixtures for:

- prompt injection in summaries, paths, errors, and test names;
- nonexistent evidence citations;
- conflicting evidence;
- missing evidence and abstention;
- oversized bundles;
- malformed structured output;
- provider timeouts and partial output;
- same-model self-evaluation; and
- provider disagreement.

LLM judgments are unsuitable for organizational comparison until they are calibrated against consented human review and project-volume strata.
