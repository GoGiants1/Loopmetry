# Loopmetry

**Local-first, evidence-backed evaluation for human–agent software work.**

Loopmetry turns normalized AI coding-workflow events into a project report with transparent metric cards, evidence references, confidence values, and measurement gaps.

It evaluates the **project workflow**, not the developer.

## Why this exists

Most coding-agent analytics products fall into one of two categories:

1. transcript viewers and token dashboards; or
2. personality profiles that assign a builder archetype or a single score.

Loopmetry takes a different approach. It asks:

> Can we trace how a requirement became a plan, a code change, verification evidence, and a delivered result?

The first implementation focuses on four deterministic project-level metrics:

- **Intent & Evidence Traceability** — requirements → plans → changes → verification → commits
- **Verification Rigor** — whether changed work is followed by successful and sufficiently broad checks
- **Recovery Efficiency** — whether recorded errors are resolved without repeated failed retries
- **Change Discipline** — requirement linkage, edit convergence, revert avoidance, and delivery completion

Human steering is reported as a **non-scored workflow signal**. A long-leash workflow and an interactive workflow can both be appropriate; intervention frequency is not treated as inherently good or bad.

## Current status

Loopmetry is an early vertical slice. It currently provides:

- a canonical JSONL event schema;
- strict schema validation;
- deterministic, inspectable metric calculations;
- evidence references and confidence values for every metric;
- a local SQLite evidence store;
- JSON, Markdown, and self-contained HTML reports;
- a zero-network standard-library runtime; and
- a demo project plus unit tests.

Agent-specific transcript adapters are the next implementation milestone. The current release expects an adapter or instrumentation layer to emit normalized events.

## Quick start

Requirements: `uv` and Python 3.11 or newer. `uv` will create and manage the project environment.

```bash
git clone https://github.com/GoGiants1/Loopmetry.git
cd Loopmetry
uv sync --all-groups
```

Validate and analyze the included demo:

```bash
uv run loopmetry validate examples/demo_project.jsonl
uv run loopmetry analyze examples/demo_project.jsonl --format markdown
uv run loopmetry analyze examples/demo_project.jsonl --format json --output report.json
uv run loopmetry analyze examples/demo_project.jsonl --format html --output report.html
```

Persist evidence locally and generate the report later:

```bash
uv run loopmetry ingest examples/demo_project.jsonl
uv run loopmetry projects
uv run loopmetry report demo-expense-cli --format markdown --output report.md
```

The default database is `.loopmetry/loopmetry.db`. Override it with `--db`.

## Event shape

Each JSONL line is one normalized event:

```json
{
  "schema_version": "0.1",
  "event_id": "evt-014",
  "project_id": "demo-expense-cli",
  "session_id": "session-2",
  "timestamp": "2026-08-22T10:03:00Z",
  "type": "file_change",
  "actor": "agent",
  "source": "demo",
  "data": {
    "path": "src/cli.py",
    "action": "modify",
    "requirement_ids": ["REQ-2"],
    "summary": "Handle FileNotFoundError with an actionable message"
  }
}
```

See [`docs/event-schema.md`](docs/event-schema.md) for the full contract.

## Design principles

### Evidence before interpretation

Metric inputs are observable events such as file changes, verification results, errors, human interventions, and commits. A report can always point back to the event IDs that affected it.

### No hidden overall rank

Loopmetry deliberately does not produce a universal developer score. Metrics have different meanings, assumptions, and confidence levels, so they remain separate.

### Deterministic core

The evaluator does not require an LLM. The same event set produces the same metric values. An optional LLM judge is planned as a separate, versioned interpretation layer; it will never replace the deterministic measurements.

### Local by default

The current runtime makes no network calls and includes no telemetry. SQLite and generated reports remain on the user's machine.

### Gaps are first-class output

Missing requirements, plans, verification events, commits, or adapter provenance lower confidence or appear explicitly as measurement gaps. Missing evidence is never silently converted into a claim about quality.


## Visualization

`--format html` writes a portable report with inline CSS, inline JavaScript, metric drill-down, evidence references, confidence, and measurement gaps. It does not load a CDN or make a network request. This static report is the default visualization surface while the evidence graph and comparison workflows stabilize. A React application is planned only for interactions that need a long-lived local service, such as timeline filtering, evidence-graph traversal, multi-run comparison, and outbound LLM-payload preview. See [`docs/dashboard.md`](docs/dashboard.md).

## Optional LLM evaluation

The planned LLM layer will build a bounded, allowlisted evidence bundle and invoke the user's locally installed Codex or Claude Code CLI with structured output. A local CLI is only the transport and authentication surface: inference may still be remote, so outbound data must be previewed and explicitly approved. Codex OSS mode can be offered separately for Ollama or LM Studio, with the actual backend recorded in the judge run.

The design keeps judge output separate from deterministic metrics, runs judges in fresh read-only ephemeral sessions, and requires evidence IDs, counterevidence, confidence, missing evidence, and human-review flags. See [`docs/llm-evaluation.md`](docs/llm-evaluation.md), [`rubrics/project-work-v1.md`](rubrics/project-work-v1.md), and [`schemas/llm-evaluation-v1.schema.json`](schemas/llm-evaluation-v1.schema.json).

## Repository layout

```text
src/loopmetry/
  schema.py       canonical event model and validation
  io.py           JSONL loading and project selection
  storage.py      local SQLite evidence store
  evaluation.py   deterministic project metrics
  report.py       JSON, Markdown, and self-contained HTML rendering
  cli.py          command-line interface

AGENTS.md             shared coding-agent instructions
CLAUDE.md              imports AGENTS.md for Claude Code

docs/
  architecture.md
  event-schema.md
  dashboard.md
  llm-evaluation.md
  metrics.md
  roadmap.md

rubrics/               versioned LLM evaluation rubrics
schemas/               stable machine-readable contracts
```

## Safety and responsible use

Loopmetry is intended for:

- project retrospectives;
- self-review of an AI-assisted workflow;
- evaluating whether project evidence is complete;
- improving specifications, verification, and recovery loops; and
- comparing workflow configurations on controlled tasks.

It is not designed for automated hiring, termination, compensation, promotion, employee ranking, or covert workplace surveillance. See [`RESPONSIBLE_USE.md`](RESPONSIBLE_USE.md). The independent implementation boundary and source provenance are documented in [`PROVENANCE.md`](PROVENANCE.md).

## Development

```bash
uv sync --all-groups
uv run python -m unittest discover -s tests -v
uv run loopmetry validate examples/demo_project.jsonl
uv run loopmetry analyze examples/demo_project.jsonl --format json
uv run loopmetry analyze examples/demo_project.jsonl --format html --output /tmp/loopmetry-report.html
uv build
```

The package has no runtime dependencies outside the Python standard library.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
