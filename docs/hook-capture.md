# Claude Code and Codex hook capture

## Status

Loopmetry can capture a privacy-minimized subset of official Claude Code and Codex lifecycle hook payloads directly into the canonical JSONL event schema.

```bash
loopmetry capture-hook --source claude-code
loopmetry capture-hook --source codex
```

The command reads one JSON object from standard input, writes no standard output by default, and appends normalized events under:

```text
<project-cwd>/.loopmetry/hooks/claude-code.jsonl
<project-cwd>/.loopmetry/hooks/codex.jsonl
```

Use `--project-id` to avoid the default pseudonymous ID derived from the working directory. Use `--output` to choose another local JSONL destination.

## Relationship to historical backfill

Hook capture is the recommended prospective path, not a prerequisite for analysis (decision D-011). A consented historical-backfill adapter (planned) can recover existing Claude Code and Codex sessions without prior setup, and a hybrid `loopmetry run --source auto` mode will merge both paths into the same canonical event schema. Installing hooks improves forward capture stability and privacy minimization at the point of collection; skipping them only means analysis relies on backfill or explicit input.

A planned `loopmetry integrate <source> --preview|--apply|--remove` command will generate the configurations below deterministically, previewing before writing and never overwriting an existing file without an explicit force option. Until it ships, use the manual examples that follow.

## Privacy boundary

The capture adapter deliberately does not retain:

- raw user prompt text;
- agent response text;
- source-code or patch bodies;
- complete tool output;
- full shell command text;
- environment variables or credentials;
- absolute path prefixes; or
- Git author identity and commit message.

It may retain:

- a one-way prompt or command hash and character count;
- repository-relative file paths;
- a content-minimized command label such as `pytest`, `lint`, or `git commit`;
- success, failure, verification kind, and generic error evidence;
- the resulting Git commit SHA when a local commit succeeds; and
- session, model, permission-mode, and lifecycle metadata when supplied by the hook.

Hashes are correlation aids, not anonymization guarantees. Local project names and relative paths can still be sensitive.

## Installing the hook command

From a clone of Loopmetry:

```bash
uv sync --locked
uv tool install --editable .
loopmetry --version
```

The editable tool install makes `loopmetry` available to hook subprocesses without depending on the current shell being inside the repository's `.venv`.

## Claude Code configuration

A project-local `.claude/settings.local.json` can capture selected events:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry capture-hook --source claude-code --project-id my-project"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry capture-hook --source claude-code --project-id my-project"
          }
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry capture-hook --source claude-code --project-id my-project"
          }
        ]
      }
    ],
    "TaskCompleted": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry capture-hook --source claude-code --project-id my-project"
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry capture-hook --source claude-code --project-id my-project"
          }
        ]
      }
    ]
  }
}
```

The hook performs no LLM call. It only normalizes and appends evidence, keeping the hook execution short.

## Codex configuration

A project-local `.codex/config.toml` can use the same capture command:

```toml
[[hooks.UserPromptSubmit]]

[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "loopmetry capture-hook --source codex --project-id my-project"
timeout = 3

[[hooks.PostToolUse]]

[[hooks.PostToolUse.hooks]]
type = "command"
command = "loopmetry capture-hook --source codex --project-id my-project"
timeout = 3

[[hooks.SessionEnd]]

[[hooks.SessionEnd.hooks]]
type = "command"
command = "loopmetry capture-hook --source codex --project-id my-project"
timeout = 3
```

Review project-local hooks in the agent UI before trusting the repository. Keep capture timeouts short; the hook performs no model call or long-running analysis.

Codex explicitly treats its transcript file format as unstable. Hook-first capture therefore forms the preferred forward-compatible path; raw rollout parsing remains a separate backfill adapter.

## One-command analysis and submission

At the end of the project, `loopmetry run` discovers both Claude Code and Codex capture files, merges duplicate event IDs, writes local JSON/HTML reports, and creates `submission.json`:

```bash
loopmetry run \
  --assignment-id agent-ai-2026 \
  --submitter-id S001
```

Add `--server` and an enrollment token environment variable to upload the same package. Use repeated `--input` only when explicit files are preferred over discovery.

Captured hooks do not yet infer requirements or acceptance criteria from free-form prompts. Consequently, traceability confidence can remain low until explicit requirement import and Git/spec enrichers are implemented.

## Current coverage

| Evidence | Claude Code | Codex | Notes |
|---|---|---|---|
| Session/task lifecycle | Supported | Supported where emitted | Stored as non-scored notes; includes `TaskCompleted` |
| User interaction | Hashed metadata | Hashed metadata | Prompt content omitted |
| File reads | Supported when path supplied | Supported when path supplied | Relative paths only |
| File changes | Write/Edit/apply-patch family | apply-patch/write family | Patch body omitted |
| Commands | Content-minimized label | Content-minimized label | Full command omitted |
| Test/lint/type/build/security checks | Heuristic command classification | Heuristic command classification | Handles direct and bounded nested response status; ambiguity remains a gap |
| Errors | Tool failure or non-zero command | Tool failure or non-zero command | Output body omitted |
| Commits | Local HEAD after successful `git commit` | Same | Message and author omitted |
| Requirements and acceptance criteria | Not inferred | Not inferred | Planned explicit importer |
| Subagent details | Lifecycle note only | Lifecycle note only | Delegation graph planned |

Every unsupported or ambiguous source behavior should remain a measurement gap rather than a fabricated event.
