# Visualization architecture

## Decision

Loopmetry uses a **self-contained static HTML report first** and reserves a React application for workflows that genuinely require a long-lived interactive client.

The evaluator's JSON result remains the authoritative contract. Markdown, static HTML, and a future React client are renderers over that contract; they must not recompute metrics or change judgments.

## Static HTML report

The CLI can produce a portable report:

```bash
uv run loopmetry analyze examples/demo_project.jsonl \
  --format html \
  --output report.html
```

The report contains:

- project snapshot cards;
- independent metric cards;
- confidence bars and metric components;
- expandable canonical evidence references;
- metric-specific and project-wide measurement gaps;
- the non-scored steering signal; and
- the complete report JSON embedded for export.

All CSS and JavaScript are inline. The document does not load a font, CDN, analytics script, or remote asset. It can be opened directly from disk and printed or shared as one file.

## Why HTML before React

The current report is a bounded, read-only artifact. Static HTML is therefore preferable because it has:

- no Node.js runtime or second dependency graph;
- no local server requirement;
- a small attack surface;
- straightforward offline behavior;
- easy archival alongside the evaluated project; and
- deterministic rendering from one JSON payload.

Introducing React before the evidence graph and comparison contracts stabilize would create UI coupling to provisional data structures.

## React threshold

Create `web/` as a separate workspace when at least one of these capabilities is implemented:

1. timeline exploration across many sessions;
2. interactive `Spec → Task → File → Test → Commit` graph traversal;
3. requirement, session, repository, or time-range filtering;
4. comparison of multiple project runs or judge runs;
5. exact outbound LLM-evaluation payload preview and consent;
6. live ingestion status; or
7. large reports that need virtualization or incremental loading.

The React client should consume a versioned JSON API or exported report bundle. It should not import Python internals or duplicate metric formulas.

## Proposed future layout

```text
src/loopmetry/       Python ingestion, storage, evaluation, API
web/                 React + TypeScript client
schemas/             shared JSON contracts
```

Recommended boundaries:

- Python owns adapters, evidence normalization, persistence, deterministic metrics, LLM-provider execution, and policy enforcement.
- React owns navigation, filtering, graph layout, comparison, and presentation.
- JSON Schema owns the contract between them.
- The default server binds only to `127.0.0.1` and enables no telemetry.

## Security requirements

- Escape all evidence text before inserting it into HTML.
- Serialize embedded JSON so `</script>` and related characters cannot terminate the data element.
- Do not render raw source code or prompts by default.
- Treat evidence summaries and future LLM output as untrusted content.
- Never execute report-provided HTML, Markdown extensions, or JavaScript.
- Keep network access explicit and separate from report viewing.
