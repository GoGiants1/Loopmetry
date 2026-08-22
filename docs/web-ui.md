# Visualization and web UI architecture

## Current decision

Loopmetry uses two dependency-free HTML surfaces before adding React:

1. a standalone participant report rendered from one deterministic project report; and
2. a server-rendered administrator dashboard backed by the roster/submission SQLite database.

JSON remains authoritative. HTML must never recompute metric values or change review semantics.

## Standalone participant report

```bash
uv run loopmetry analyze examples/demo_project.jsonl \
  --format html \
  --output report.html
```

The file contains project snapshot cards, independent metric cards, confidence, components, evidence references, gaps, and the non-scored steering signal. CSS is inline; there are no external scripts, fonts, analytics, or network requests.

## Administrator dashboard

```bash
LOOPMETRY_ADMIN_TOKEN='long-random-secret' \
uv run loopmetry admin serve --db .loopmetry/admin.db
```

The dashboard provides:

- full-roster visibility, including `not_submitted` participants;
- assignment, state, and identity filters;
- latest-attempt summary without hiding attempt history;
- metric score and confidence chips;
- measurement-gap count;
- submission detail and validated envelope inspection;
- manual `received`, `reviewing`, `needs_revision`, and `accepted` state;
- reviewer notes and append-only state history; and
- CSV export.

It orders by roster identity, not by metric score. No client-side JavaScript is required.

## JSON APIs

A future client can consume:

```text
GET /api/v1/participants
GET /api/v1/submissions
GET /api/v1/submissions/<submission-id>
```

The participant upload contract is separate:

```text
POST /api/v1/submissions
```

The external submission schema is `schemas/submission-v1.schema.json`.

## Why React is deferred

The current administrator interaction is bounded and table-oriented. Server-rendered HTML provides:

- a single Python runtime and dependency graph;
- local/offline deployment;
- a smaller attack surface;
- simple HTTP Basic authentication;
- deterministic rendering; and
- no frontend build requirement for early pilots.

Introducing React now would couple a second implementation to provisional evidence-graph and cohort-comparison contracts.

## React threshold

Create `web/` as a separate TypeScript workspace when one or more of these become necessary:

1. thousands of participants or evidence nodes requiring virtualization;
2. interactive Spec → Task → File → Test → Commit graph traversal;
3. multi-attempt or controlled-run comparison;
4. reviewer queues, assignment-level annotations, or real-time collaboration;
5. live capture and upload progress;
6. rich outbound LLM-payload review; or
7. organization SSO and role-sensitive navigation.

## Future boundary

```text
src/loopmetry/       capture, evaluation, submission, policy, API
web/                 React + TypeScript presentation
schemas/             shared versioned contracts
```

Python owns metric formulas, evidence policy, authentication decisions, persistence, and review-state validation. React owns navigation, filtering, graph layout, and presentation.

## Security requirements

- Treat evidence summaries, reviewer notes, and future model output as untrusted text.
- Escape all HTML and never execute report-provided markup.
- Keep state-changing operations CSRF-protected.
- Do not place participant or administrator tokens in URLs.
- Bind the built-in server to loopback by default.
- Use HTTPS and a hardened reverse proxy for network deployments.
- Preserve no-store and restrictive content-security headers.
