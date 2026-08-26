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

Hook capture is the recommended prospective path, not a prerequisite for analysis (decision D-011). Consented historical-backfill adapters recover existing Claude Code and Codex sessions without prior setup. `loopmetry run --source auto` actively imports consented Claude Code history and merges it with hook, explicit, and previously imported Codex history evidence; Codex history can be imported first with `loopmetry history import --source codex`. Installing hooks improves forward capture stability and privacy minimization at the point of collection; skipping them only means analysis relies on backfill or explicit input.

Discovery is bounded to this project's own sessions under `~/.claude/projects/<encoded-project-root>/`, confirmed from each session's recorded `cwd` rather than the (lossy) directory name alone:

```bash
loopmetry history preview --source claude-code
loopmetry history import --source claude-code --since 2026-08-01
loopmetry history preview --source codex
loopmetry history import --source codex --since 2026-08-01
```

`preview` is read-only and lists candidate sessions and discovery diagnostics without importing anything. `import` requires consent — an interactive TTY confirms before reading, and a non-interactive run requires `--yes`. `--since YYYY-MM-DD` narrows the discovery window. Imported events land in `.loopmetry/events/claude-code-history.jsonl` or `.loopmetry/events/codex-history.jsonl` with `history-backfill` provenance, and per-source checkpoints under `.loopmetry/checkpoints/` make re-import incremental. Unresolved tool calls remain checkpointed until a later result arrives or the session is confirmed stalled (decision D-013).

`loopmetry integrate <source> --preview|--apply|--remove` generates the configuration below deterministically instead of copy-pasting it by hand (supported sources: `claude-code`, `codex`):

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
run). An existing file that isn't valid JSON, or whose `hooks` value (or a
targeted event's value) is not the expected JSON shape, is a hard error on
every mode, never silently ignored or partially applied.

The merge is structural and idempotent: each hook event gets **exactly one**
managed block, generated in exec form (`"command": "loopmetry"` with an
`"args"` array) rather than a single shell-parsed string, so a `--project-id`
containing spaces or shell metacharacters is passed through as one argument
and never interpreted by a shell. Re-running `--apply` with a different
`--project-id` replaces that one block in place rather than adding a second
handler (and collapses any pre-existing duplicates down to one). `--remove`
only ever touches the five events this installer manages, and only strips a
block that structurally matches exactly what this installer would generate —
a block scoped by `matcher` (fires for only a subset of the event's
occurrences) or a handler with an `if` condition is never mistaken for full
integration by `--apply`, and is never removed by `--remove`. Everything else
already in the file — other settings, other hooks, other events — is left
untouched. Pass `--project-id` to embed a fixed `--project-id` in the
generated command (otherwise `capture-hook`'s own default derivation applies
at hook-run time).

`integrate codex` targets `<root>/.codex/config.toml` instead of
`.claude/settings.local.json`. Because Codex's hook config has no exec-form
`args` array, the installer embeds the whole invocation as a single
shell-parsed `command` string, and protects a `--project-id` containing
spaces or shell metacharacters with `shlex.quote()` rather than exec-form
argument separation — a different safety mechanism from the JSON installer's,
worth calling out explicitly rather than treating the two sources as
interchangeable. Like the JSON path, it backs up an existing file to
`config.toml.bak` before any modifying write, and it hard-errors on invalid
existing TOML (or a `hooks` table/array shape it doesn't recognize) rather
than overwriting it.

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

`loopmetry integrate claude-code --apply --project-id my-project` writes this
to a project-local `.claude/settings.local.json` (exec form — `args` is
passed straight to the process, never re-parsed by a shell, so a project ID
with spaces or shell metacharacters stays a single argument):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry",
            "args": ["capture-hook", "--source", "claude-code", "--project-id", "my-project"]
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry",
            "args": ["capture-hook", "--source", "claude-code", "--project-id", "my-project"]
          }
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry",
            "args": ["capture-hook", "--source", "claude-code", "--project-id", "my-project"]
          }
        ]
      }
    ],
    "TaskCompleted": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry",
            "args": ["capture-hook", "--source", "claude-code", "--project-id", "my-project"]
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "loopmetry",
            "args": ["capture-hook", "--source", "claude-code", "--project-id", "my-project"]
          }
        ]
      }
    ]
  }
}
```

The hook performs no LLM call. It only normalizes and appends evidence, keeping the hook execution short.

## Codex configuration

`loopmetry integrate codex --apply --project-id my-project` is the preferred
way to generate a project-local `.codex/config.toml`; hand-editing is shown
below only to make the resulting shape explicit. It uses the same capture
command:

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

`--source auto` additionally triggers a consented Claude Code history scan as
part of the same command, merging it with hook and explicit evidence:

```bash
loopmetry run --source auto --since 2026-08-01 --until 2026-08-31 \
  --assignment-id agent-ai-2026 --submitter-id S001
```

In an interactive terminal this asks the same two confirmation questions as
`loopmetry history import` (scan, then proceed with N sessions). In a
non-interactive shell, history is included only when `--include-history` is
passed; its absence is not an error — the run proceeds with hook and explicit
evidence only, since `run` is the one-command path and must not abort over an
omitted optional flag. `--since`/`--until` (`YYYY-MM-DD`) bound the history scan
for this invocation only; there is no default assignment-window bound yet (that
part of decision D-012 remains blocked on assignment-schema work — see
`docs/decision-log.md` D-015).

Cross-source disagreements — the same `event_id` observed with different
content from, say, a hook and a history-backfill import — never abort a
`--source auto` run. They surface as an `adapter_conflict` diagnostic (first
observation kept) printed to stdout as `source diagnostics: adapter_conflict=N`
and recorded in `manifest.json`'s `source_coverage` block:

```json
{
  "source_coverage": {
    "mode": "auto",
    "history_included": true,
    "diagnostics": [
      {"kind": "adapter_conflict", "summary": "...", "count": 1}
    ]
  }
}
```

`report.json`/`report.html` do not yet surface source coverage — that is
roadmap milestone 2 slice 6's job. Without `--source auto`, `run`'s behavior,
output, and `manifest.json` shape are unchanged from before this feature.

Captured hooks do not yet infer requirements or acceptance criteria from free-form prompts. Consequently, traceability confidence can remain low until explicit requirement import and Git/spec enrichers are implemented.

## Current coverage

| Evidence | Claude Code | Codex | Notes |
|---|---|---|---|
| Session/task lifecycle | Supported | Supported where emitted | Stored as non-scored notes; includes `TaskCompleted` |
| User interaction | Hashed metadata | Hashed metadata | Prompt content omitted |
| File reads | Supported when path supplied | Supported when path supplied | Relative paths only |
| File changes | Write/Edit/apply-patch family | apply-patch/write family (hook); apply-patch family (history)[^codex-history] | Patch body omitted |
| Commands | Content-minimized label | Content-minimized label (status unknown in history)[^codex-history] | Full command omitted |
| Test/lint/type/build/security checks | Heuristic command classification | Hook capture only; history excludes verification evidence[^codex-history] | Handles direct and bounded nested response status; ambiguity remains a gap |
| Errors | Tool failure or non-zero command | Hook capture only; history excludes exit-code signals[^codex-history] | Output body omitted |
| Commits | Local HEAD after successful `git commit` | Same | Message and author omitted |
| Requirements and acceptance criteria | Not inferred | Not inferred | Planned explicit importer |
| Subagent details | Lifecycle note only | Lifecycle note only | Delegation graph planned |

[^codex-history]: Codex's historical-backfill adapter (`adapters/codex_history.py`) can extract commands and apply_patch file changes from rollout files, but Codex's rollout format never persists a command exit-code or success signal (confirmed against `openai/codex` source) — every backfilled `command` event's status is `"unknown"`, distinguishing it from live hook capture, which does get a real exit status. Session attribution is by `session_meta.cwd`, the same scoping Claude Code's adapter uses; unattributed sessions are excluded, not widened into scope.

Every unsupported or ambiguous source behavior should remain a measurement gap rather than a fabricated event.
