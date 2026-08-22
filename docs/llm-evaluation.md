# Optional LLM evaluation design

## Objective

Loopmetry's deterministic metrics measure observable workflow evidence. An optional LLM layer can evaluate constructs that are difficult to encode as fixed formulas, such as goal fidelity, adequacy of verification for a particular change, recovery reasoning, and risk awareness.

The LLM layer must not replace deterministic metrics. It produces a separate, versioned judgment with evidence citations, confidence, missing evidence, and a human-review flag.

## Three meanings of “local”

Loopmetry should distinguish three independent properties:

1. **Local session data**: Claude Code or Codex transcripts and hook events stored on the user's machine.
2. **Local CLI transport**: invoking the user's installed and authenticated `claude` or `codex` executable.
3. **Inference location**: a remote provider model or an on-device backend.

A local CLI is not automatically on-device inference. The default authenticated Codex and Claude Code paths normally send the evaluation bundle to a model provider, so the exact outbound payload must be previewed and explicitly approved. Codex can also use an OSS backend through Ollama or LM Studio; that must be exposed as a separate capability and recorded in judge metadata.

## Proposed data flow

```text
Claude Code / Codex local session
        │
        ├── official lifecycle hook payload
        │     session_id / transcript_path / cwd
        │
        └── explicit offline import
                      │
                      ▼
            source-specific adapter
                      │
                      ▼
            canonical evidence events
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
 deterministic metrics     evaluation bundle builder
                                  │
                                  ▼
                      redaction + size budget + preview
                                  │
                         ┌────────┴────────┐
                         ▼                 ▼
                  Codex CLI judge   Claude CLI judge
                         │                 │
                         └────────┬────────┘
                                  ▼
                     schema-validated LLM result
                                  │
                                  ▼
                    merged project report, still
                    separating facts from judgments
```

## Session capture

### Preferred: lifecycle hooks

Both integrations should install a command hook that invokes a Loopmetry capture command with JSON on stdin. The hook payload provides the session ID, transcript path, working directory, and event name, avoiding hard-coded directory discovery.

Recommended events:

- `SessionEnd`: final offline import and evaluation trigger;
- `PostToolUse`: optional near-real-time normalized evidence capture;
- `PostToolUseFailure`: optional error capture;
- `PreCompact`: optional checkpoint before a transcript is compacted.

Hooks must be installed only after showing the exact configuration diff. Project-local configuration should be the default for development and pilots; user-global configuration should require an explicit flag.

A lifecycle hook must **not** parse a large transcript or call a judge synchronously. It should validate the locator payload, write a small spool record, and return quickly. A separate Loopmetry worker performs import, normalization, bundling, and optional judging. This avoids blocking the source agent and accommodates short hook timeouts.

Hook payloads are locators, not stable transcript schemas. In particular, provider transcript formats may change. Adapters must therefore be versioned, fixture-tested, and able to return partial coverage with warnings rather than silently mis-parsing a session.

### Fallback: offline discovery

Directory scanning remains a compatibility fallback for pre-existing sessions. Every adapter should expose a coverage matrix and record its parser version, source version, warnings, and unparsed event counts.

## Deterministic extraction versus semantic enrichment

The first adapter pass should be deterministic: parse timestamps, actors, tool calls, tool results, file paths, exit status, tests, errors, commits, and source metadata directly from the provider record.

A later optional semantic-enrichment pass may propose requirement links, plan summaries, recovery episodes, or acceptance-criterion mappings that are not explicit in the source. Those proposals must be stored as inferred evidence with their own provider, model, prompt hash, confidence, and source evidence IDs. They must never overwrite deterministic events or silently become ground truth.

This separation prevents an LLM parser and an LLM judge from reinforcing the same unsupported interpretation. The default v1 judge bundle should use deterministic evidence plus explicitly labeled human-authored requirements; inferred enrichment is opt-in.

## Evaluation bundle

The judge should receive a bounded JSON document, not the raw transcript. The bundle should contain:

- bundle, schema, adapter, and rubric versions;
- project, session, requirement, and evidence IDs;
- requirement and acceptance-criterion summaries;
- normalized plans, file-change summaries, commands, verifications, errors, recoveries, and commits;
- deterministic factual measurements without their interpreted scores in blind-judge mode;
- bounded Git diff statistics and selected verification output;
- source coverage and measurement gaps; and
- an outbound-data manifest listing every included field.

Raw prompts, full agent messages, source-code bodies, credentials, author email, remote URLs, and absolute private paths are excluded by default. Optional excerpts require a separate consent flag.

A bundle is content-addressed. Its hash, rubric hash, provider configuration, and output hash form the cache and audit key.

## Judge provider interface

A provider implementation should expose conceptually:

```text
probe() -> ProviderCapabilities
evaluate(bundle, rubric, output_schema, options) -> JudgeRun
```

`ProviderCapabilities` records executable path, CLI version, authentication readiness, backend, inference location, structured-output support, supported isolation flags, and cost metadata availability.

`JudgeRun` records:

- provider and model;
- CLI and Loopmetry versions;
- rubric, prompt, schema, and bundle hashes;
- start/end times and exit status;
- stdout/stderr diagnostics;
- token usage and cost when available; and
- the validated result or a typed failure.

Provider-specific output must be normalized before storage. Source adapters and judge providers are independent: a Claude Code session can be evaluated by Codex, a Codex session can be evaluated by Claude, and either can be evaluated by another provider. Cross-provider judging is preferred for calibration because it reduces same-system self-evaluation bias.

## Codex CLI provider

Recommended execution properties:

- invoke `codex exec` with the user's saved CLI authentication for the remote-provider mode;
- use `--ephemeral` so the judge run does not create another persistent rollout;
- force `--sandbox read-only`;
- use `--ignore-user-config` and `--ignore-rules`, and disable instruction discovery through a probed isolation profile; do not assume these flags alone suppress a global `AGENTS.md`;
- pass the bundle through stdin;
- require `--output-schema schemas/llm-evaluation-v1.schema.json`;
- use `--skip-git-repo-check` and execute from a temporary directory containing only the bundle, rubric, and schema; and
- optionally expose a separate `codex-oss` mode using `--oss --local-provider ollama|lmstudio`, after probing that the selected local model can follow the result schema reliably.

The provider should parse the final structured response, while optionally retaining the JSONL event stream for diagnostics without mixing it into project evidence. The run must start a new ephemeral thread; it must never resume or append to the source session. Capability probing should inspect the effective model input and refuse to claim isolation when user-level instructions remain active.

Conceptual invocation:

```bash
codex exec --ephemeral --json \
  --output-schema schemas/llm-evaluation-v1.schema.json \
  --sandbox read-only \
  --ignore-rules --ignore-user-config --skip-git-repo-check \
  --cd <isolated-temp-dir> - < evaluation-request.txt
```

## Claude Code CLI provider

Recommended execution properties:

- invoke `claude -p` with the user's existing authentication;
- use `--bare` to skip project hooks, skills, plugins, MCP servers, memory, and `CLAUDE.md`;
- use `--no-session-persistence` so the judge does not create another saved conversation;
- use `--tools ""` and deny MCP tools so the judge cannot modify or inspect the repository beyond the supplied bundle;
- use `--max-turns 1` for a static evidence judgment;
- request `--output-format json` with `--json-schema`; and
- execute from the same kind of isolated temporary directory.

If repo-aware evaluation is added later, expose it as a separate mode with read-only tools and an explicit file allowlist. The run must start a fresh non-persistent conversation; it must never resume the source session.

Conceptual invocation:

```bash
claude -p --bare --no-session-persistence \
  --tools "" --disallowedTools "mcp__*" --strict-mcp-config \
  --max-turns 1 --output-format json \
  --json-schema "$(cat schemas/llm-evaluation-v1.schema.json)" \
  < evaluation-request.txt
```

Provider adapters must probe the installed CLI because flags and output envelopes can change across versions. Unsupported isolation or structured-output capability is a typed failure, not a reason to fall back to a less safe invocation.

## Output contract

The initial result contract is `schemas/llm-evaluation-v1.schema.json`. The default rubric is `rubrics/project-work-v1.md`.

Each dimension contains:

- ordinal rating `0..4` or `null` when not assessable;
- assessability and confidence;
- rationale;
- supporting and counterevidence IDs; and
- missing evidence.

The top-level verdict is `pass`, `partial`, `fail`, or `indeterminate`. It is a project-completion judgment, not an employee score. No overall numeric score is produced.

## Prompt-injection boundary

All transcript-derived text is untrusted evidence, not instruction. The provider prompt must state that commands or policy text found inside the bundle must never be followed. The bundle builder should normalize control-like content, cap every free-text field, and prefer factual summaries and evidence IDs over raw excerpts. Judges run without tools, repository access, hooks, plugins, or MCP servers, so a malicious session cannot turn an evaluation into another agent run. Adversarial fixtures must include instructions embedded in prompts, command output, file names, test names, and error messages.

## Bias and leakage controls

- Run the judge in a fresh session, never by resuming the session being evaluated.
- Do not load repository `AGENTS.md`, `CLAUDE.md`, skills, hooks, plugins, MCP servers, or personal memory into the judge.
- Default to blind judging: omit deterministic metric scores so they do not anchor the LLM.
- Preserve `producer_provider`, `producer_model`, `judge_provider`, and `judge_model` in metadata.
- Derive and store a `self_judge` flag when provider/model lineage overlaps.
- Support cross-provider and repeated judging, but report disagreement rather than hiding it in an average.
- Do not expose identity, employment data, or author names to the rubric.
- Reject any supporting or counterevidence ID that is not present in the submitted bundle. Unknown IDs invalidate the judge result rather than becoming new evidence.
- Keep deterministic scores hidden in blind-judge mode; provide factual counts only when the rubric needs them.

## CLI surface

A future implementation can use this shape:

```bash
loopmetry integrate claude-code --scope project --dry-run
loopmetry integrate codex --scope project --dry-run
loopmetry import-session --source claude-code --transcript <path>
loopmetry bundle <project-id> --scope project --output bundle.json
loopmetry judge <project-id> --provider codex --rubric project-work-v1 --preview
loopmetry judge <project-id> --provider codex-oss --local-provider ollama --rubric project-work-v1 --preview
loopmetry judge <project-id> --provider claude --rubric project-work-v1 --preview
loopmetry report <project-id> --include-llm
loopmetry doctor --judges
```

`--preview` should be required on the first network-backed run and remain available thereafter.

## Implementation sequence

1. Define adapter and provider protocols plus typed error models.
2. Implement hook payload capture and offline import for Claude Code.
3. Implement the corresponding Codex capture path.
4. Build the bounded, content-addressed evaluation bundle and payload preview.
5. Implement Codex CLI structured-output judging.
6. Implement Claude Code CLI structured-output judging.
7. Store judge runs separately from deterministic metric results.
8. Validate every returned evidence ID against the exact bundle and store judge runs in an append-only audit table.
9. Add synthetic fixtures for prompt injection, missing evidence, conflicting evidence, oversized bundles, malformed provider output, and provider timeouts.
10. Calibrate against consented human judgments before using verdicts for organizational comparison.
