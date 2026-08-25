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

Hook capture is the recommended prospective path, not a prerequisite for analysis (decision D-011). A consented historical-backfill adapter recovers existing Claude Code sessions without prior setup (Codex backfill remains planned, slice 5), and a hybrid `loopmetry run --source auto` mode will merge both paths into the same canonical event schema (slice 4). Installing hooks improves forward capture stability and privacy minimization at the point of collection; skipping them only means analysis relies on backfill or explicit input.

Discovery is bounded to this project's own sessions under `~/.claude/projects/<encoded-project-root>/`, confirmed from each session's recorded `cwd` rather than the (lossy) directory name alone:

```bash
loopmetry history preview --source claude-code
loopmetry history import --source claude-code --since 2026-08-01
```

`preview` is read-only and lists candidate sessions and discovery diagnostics without importing anything. `import` requires consent — an interactive TTY confirms before reading, and a non-interactive run requires `--yes` (per invariant 10, `loopmetry run` never triggers a history read implicitly). `--since YYYY-MM-DD` narrows the discovery window. Imported events land in `.loopmetry/events/claude-code-history.jsonl` with `history-backfill` provenance, and a checkpoint under `.loopmetry/checkpoints/` makes re-import incremental — only new transcript content is re-read, and a Bash call's outcome is written at most once (see decision D-013 for why unresolved `tool_use`/`tool_result` pairs are carried across imports instead of guessed at eagerly).

`loopmetry integrate claude-code --preview|--apply|--remove` generates the configuration below deterministically instead of copy-pasting it by hand:

```bash
loopmetry integrate claude-code --preview   # show the exact diff, write nothing
loopmetry integrate claude-code --apply     # write it (add --force if the file already exists)
loopmetry integrate claude-code --remove    # strip only the managed blocks (add --force if the file already exists)
```

`--preview` never writes. `--apply` and `--remove` only require `--force` when
`.claude/settings.local.json` already exists and the change would actually
modify it; creating the file from scratch, or re-running a command that would
produce no change, never requires `--force`. Any modification of an existing
file first writes a backup to `settings.local.json.bak` (overwritten on each
run). An existing file that isn't valid JSON is a hard error, never silently
treated as empty. The merge is structural and idempotent: it adds one managed
block per hook event without touching unrelated settings or other hooks
already in the file, and `--remove` strips only those managed blocks. Pass
`--project-id` to embed a fixed `--project-id` in the generated command
(otherwise `capture-hook`'s own default derivation applies at hook-run time).
`--source codex` is not yet supported (planned for the Codex-parity slice).

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
