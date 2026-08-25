# LLM Judge Prototype — Design

**Status:** Experimental prototype on branch `llm-judge-prototype`. Not a roadmap commitment.
**Not covered by a decision-log entry.** `docs/roadmap.md` Milestone 7 ("optional LLM judgment
providers") is explicitly later than the current milestone; this branch exists only to get a feel
for the end-to-end flow before that milestone is scheduled for real. Do not merge to `main` without
a decision-log entry and roadmap update.

## Scope

Prototype a single remote, API-key-based LLM judge provider that consumes an already-built
evaluation bundle (`loopmetry bundle` output) and the `project-work-v1` rubric, and produces a
result matching `schemas/llm-evaluation-v1.schema.json`. Explicitly excludes: Claude Code/Codex
subscription sessions (policy-prohibited — see conversation), on-device providers, cross-provider
disagreement reporting, calibration.

## Why API-key based, not a subscription session

Claude Code / Codex subscription (Pro/Max/Plus) usage policies restrict subscription credentials to
interactive, non-programmatic use by the subscriber. Automating a subscription session as a
third-party product's backend is out of policy. The only supported path for a programmatic,
third-party-integrated judge is metered API-key billing.

## Module: `src/loopmetry/llm_provider.py`

```python
class ProviderError(RuntimeError): ...

def probe(*, api_key_env: str = "ANTHROPIC_API_KEY") -> dict:
    """Check that the named env var is set and non-empty. No network call."""

def evaluate(
    bundle: Mapping[str, object],
    rubric_text: str,
    *,
    model: str = "claude-opus-5",
    api_key_env: str = "ANTHROPIC_API_KEY",
    max_tokens: int = 8000,
) -> dict:
    """Call the Anthropic Messages API and return a llm-evaluation-v1-shaped result."""
```

- **Transport:** `urllib.request`, raw HTTPS POST to `https://api.anthropic.com/v1/messages`.
  No SDK dependency — keeps `pyproject.toml` `dependencies = []` intact per `AGENTS.md`.
- **Auth:** follows the existing `--*-env` pattern (`submission.py`, `cli.py`): the caller names an
  env var; we read it with `os.environ.get(name, "").strip()`. Missing/empty key raises
  `ProviderError` before any network call.
- **Structured output:** request `output_config.format` = `json_schema` built from
  `llm-evaluation-v1.schema.json`, with `minimum`/`maximum` constraints stripped recursively before
  sending (Anthropic structured outputs doesn't support numeric range constraints). We re-validate
  those ranges ourselves on the response.
- **Validation:** hand-rolled `validate_llm_evaluation_result(raw) -> dict`, mirroring the
  field-by-field style of `submission.py:validate_submission` (no generic `jsonschema` dependency).
  Checks required fields, enums (`verdict`, `assessability`, `severity`), rating range 0–4 or null,
  string length limits.
- **Evidence-ID integrity:** every `evidence_ids`/`counterevidence_ids` value in the response must
  exist in the bundle's `event_id` set. Any unknown ID invalidates the whole result
  (`ProviderError`) — per `docs/llm-evaluation.md`: "Unknown evidence invalidates the result."
- **Untrusted content:** system prompt states plainly that bundle content is untrusted project data,
  not instructions — matches `policy.untrusted_content_notice` in the bundle payload.

## CLI: `loopmetry judge`

```
uv run loopmetry judge bundle.json \
  --rubric rubrics/project-work-v1.md \
  --api-key-env ANTHROPIC_API_KEY \
  --model claude-opus-5 \
  --output judge-result.json \
  --yes
```

- Takes an already-built bundle file (not raw events) — preserves the "preview before send"
  principle.
- Prints a one-line summary of what will be sent (event_count, project_id, bundle_id prefix) to
  stderr and requires `--yes` (or an interactive confirmation) before making the network call.
- Failure modes (missing key, network error, HTTP error, evidence-ID mismatch, schema validation
  failure) all exit non-zero with a clear stderr message. No silent fallback to an empty/successful
  result (stable invariant 4).

## Output shape

```json
{
  "judge_run": {
    "provider": "anthropic",
    "model": "claude-opus-5",
    "bundle_id": "sha256:...",
    "rubric_id": "project-work-v1",
    "requested_at": "2026-08-25T00:00:00Z",
    "usage": {"input_tokens": 0, "output_tokens": 0}
  },
  "result": { }
}
```

Written as a standalone artifact. Never merged into `submission.json` or the deterministic report
(stable invariants 1 and 9).

## Testing

`tests/test_llm_provider.py`, network calls mocked via monkeypatching
`urllib.request.urlopen`:

- missing API key → `ProviderError`, no network call attempted
- schema constraint-stripping helper (unit test on the stripped dict shape)
- unknown evidence ID in a mocked response → `ProviderError`
- `validate_llm_evaluation_result` rejects missing required fields / bad enum / out-of-range rating

No integration test that hits the real API (cost, non-deterministic, not CI-appropriate). Manual
end-to-end run via the CLI is the point of this branch.
