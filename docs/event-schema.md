# Canonical event schema v0.1

Loopmetry consumes UTF-8 JSON Lines. Each non-empty line is one event object. Comment lines beginning with `#` are ignored.

## Required envelope

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Currently `0.1` |
| `event_id` | string | Stable unique ID; used for deduplication and evidence references |
| `project_id` | string | Stable project identity across sessions |
| `session_id` | string | One human–agent work session or execution window |
| `timestamp` | ISO-8601 string | Must include a timezone; normalized to UTC |
| `type` | string | Canonical event type |
| `actor` | string | `human`, `agent`, `tool`, or `system` |
| `source` | string | Adapter provenance, such as `claude-code` or `codex` |
| `data` | object | Type-specific fields |

## Event types

### `project_start` / `project_end`

Optional project boundary events.

Suggested data:

```json
{"summary": "Start payment-service hardening"}
```

### `requirement`

Declares a requirement or acceptance target.

Required data:

```json
{
  "requirement_id": "REQ-7",
  "summary": "Reject expired access tokens"
}
```

Optional data may include `source_document`, `priority`, or `acceptance_criteria`.

### `plan`

Records a plan before implementation.

Suggested data:

```json
{
  "summary": "Inspect token validation, add a failing test, then patch the validator",
  "requirement_ids": ["REQ-7"]
}
```

### `file_read`

Records project-file inspection.

Required data:

```json
{"path": "src/auth/validator.py"}
```

### `file_change`

Records a file mutation.

Required data:

```json
{
  "path": "src/auth/validator.py",
  "action": "modify",
  "requirement_ids": ["REQ-7"]
}
```

Recommended `action` values are `add`, `modify`, `delete`, `revert`, `undo`, and `rollback`. The first release uses revert-like actions in the change-discipline metric.

### `command`

Records a shell or tool command when it matters to project evidence.

Required data:

```json
{
  "command": "python -m unittest tests.test_auth",
  "status": "success"
}
```

Allowed statuses: `success`, `failed`, `error`, and `unknown`.

### `verification`

Records tests, linting, builds, static analysis, security checks, or other completion evidence.

Required data:

```json
{
  "kind": "test",
  "status": "passed",
  "command": "python -m unittest tests.test_auth",
  "requirement_ids": ["REQ-7"],
  "paths": ["src/auth/validator.py", "tests/test_auth.py"]
}
```

Allowed statuses: `passed`, `failed`, `error`, and `skipped`.

Recommended kinds include `test`, `lint`, `build`, `typecheck`, `security`, `review`, and `evaluation`.

### `error`

Records an explicit failure that should be tracked through recovery.

At least one of `message` or `code` is required:

```json
{
  "code": "ASSERTION_ERROR",
  "message": "Expected 401 but received 200",
  "requirement_ids": ["REQ-7"]
}
```

Adapters should emit stable error codes or normalized signatures when possible. Recovery analysis searches the same session for a later successful command or passed verification.

### `human_intervention`

Records an explicit decision point without assigning a positive or negative grade.

Required data:

```json
{
  "action": "redirect",
  "summary": "Do not refactor unrelated token parsing",
  "requirement_ids": ["REQ-7"]
}
```

Suggested actions include `approve`, `review`, `checkpoint`, `accept`, `redirect`, `reject`, `stop`, and `scope_correction`.

### `commit`

Records a delivery boundary.

Required data:

```json
{
  "sha": "9d91b33",
  "summary": "fix: reject expired tokens",
  "requirement_ids": ["REQ-7"],
  "changed_files": ["src/auth/validator.py", "tests/test_auth.py"]
}
```

### `note`

Carries source-specific context that is useful for inspection but is not currently scored.

## Shared optional fields

Several event types may include:

- `requirement_id`: one linked requirement;
- `requirement_ids`: multiple linked requirements;
- `path`: one file path;
- `paths`: multiple file paths;
- `changed_files`: changed paths associated with a commit; and
- `summary`: a short content-free explanation suitable for a report.

## Privacy guidance

The core metrics do not require raw prompt text, response text, source-code excerpts, customer data, Git author email, or repository remote URLs. Adapters should omit these fields unless a separate use case explicitly requires them.

## Compatibility policy

Schema `0.1` is experimental. Breaking changes may occur before `1.0`, but each persisted event carries `schema_version` so migrations can be explicit.
