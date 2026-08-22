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
- JSON and Markdown reports;
- a zero-network standard-library runtime; and
- a demo project plus unit tests.

Agent-specific transcript adapters are the next implementation milestone. The current release expects an adapter or instrumentation layer to emit normalized events.

## Quick start

Requirements: Python 3.11 or newer.

```bash
git clone https://github.com/GoGiants1/Loopmetry.git
cd Loopmetry
python -m pip install -e .
```

Validate and analyze the included demo:

```bash
loopmetry validate examples/demo_project.jsonl
loopmetry analyze examples/demo_project.jsonl --format markdown
loopmetry analyze examples/demo_project.jsonl --format json --output report.json
```

Persist evidence locally and generate the report later:

```bash
loopmetry ingest examples/demo_project.jsonl
loopmetry projects
loopmetry report demo-expense-cli --format markdown --output report.md
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

The evaluator does not require an LLM. The same event set produces the same metric values. A future narrative layer may be added, but it will remain optional and separate from metric computation.

### Local by default

The current runtime makes no network calls and includes no telemetry. SQLite and generated reports remain on the user's machine.

### Gaps are first-class output

Missing requirements, plans, verification events, commits, or adapter provenance lower confidence or appear explicitly as measurement gaps. Missing evidence is never silently converted into a claim about quality.

## Repository layout

```text
src/loopmetry/
  schema.py       canonical event model and validation
  io.py           JSONL loading and project selection
  storage.py      local SQLite evidence store
  evaluation.py   deterministic project metrics
  report.py       Markdown and JSON rendering
  cli.py          command-line interface

docs/
  architecture.md
  event-schema.md
  metrics.md
  roadmap.md
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
python -m unittest discover -s tests -v
python -m loopmetry validate examples/demo_project.jsonl
python -m loopmetry analyze examples/demo_project.jsonl --format json
```

The package has no runtime dependencies outside the Python standard library.

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
