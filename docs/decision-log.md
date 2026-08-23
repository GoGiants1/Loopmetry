# Loopmetry decision log

This file records accepted product and architecture decisions. Read it to understand **why** the repository is shaped as it is. It does not prove that a feature is implemented; code and tests are the implementation source of truth.

Statuses:

- **Accepted** — current direction; implementation should conform.
- **Superseded** — retained for history but replaced by a later decision.
- **Proposed** — under discussion and not yet binding.

## Index

| ID | Date | Status | Decision |
|---|---|---|---|
| D-001 | 2026-08-22 | Accepted | Evaluate project workflow, not developer ability |
| D-002 | 2026-08-22 | Accepted | Deterministic evidence core; no universal overall score |
| D-003 | 2026-08-22 | Accepted | Local-first processing and data-minimized submissions |
| D-004 | 2026-08-22 | Accepted | Versioned JSON contracts; renderers do not own evaluation logic |
| D-005 | 2026-08-22 | Accepted | Use `uv`; keep the runtime standard-library-only by default |
| D-006 | 2026-08-22 | Accepted | Static HTML first; React only for multi-run interactive workflows |
| D-007 | 2026-08-22 | Accepted | One-command participant flow and administrator cohort collection are the current milestone |
| D-008 | 2026-08-22 | Accepted | Local LLM execution is deferred; preserve only extension contracts |
| D-009 | 2026-08-22 | Accepted | Submission retries are idempotent and review status is manual |
| D-010 | 2026-08-22 | Accepted | Keep agent instructions navigational; store rationale in this decision log |
| D-011 | 2026-08-23 | Accepted | Prospective hook capture and retrospective historical backfill are both first-class source paths |

---

## D-001 — Evaluate project workflow, not developer ability

**Status:** Accepted
**Context:** Coding-agent traces expose workflow evidence, but they do not establish a person's general engineering ability or employment suitability.
**Decision:** Loopmetry evaluates how project intent becomes plans, changes, verification, recovery, and delivery. It must not rank participants or produce hiring, promotion, compensation, or termination signals.
**Consequences:** Reports use project-level metric cards, confidence, evidence, and gaps. Responsible-use restrictions remain part of the product boundary.
**Related:** `RESPONSIBLE_USE.md`, `docs/metrics.md`

## D-002 — Deterministic evidence core; no universal overall score

**Status:** Accepted
**Context:** Counts, ordering, exit status, and evidence links are reproducible; a single opaque score would collapse distinct constructs and hide uncertainty.
**Decision:** Deterministic metrics remain independent. The same canonical events produce the same results, and every claim carries evidence references, confidence, and measurement gaps. No universal overall developer or participant score is produced.
**Consequences:** Semantic or LLM judgments, when added, remain a separate layer and are not silently blended into deterministic metrics.
**Related:** `docs/event-schema.md`, `docs/metrics.md`, `src/loopmetry/evaluation.py`

## D-003 — Local-first processing and data-minimized submissions

**Status:** Accepted
**Context:** Agent sessions can contain proprietary code, prompts, paths, customer information, and credentials. Administrators need submission status and evaluation results, not the full local transcript.
**Decision:** Raw session material is processed locally. Submission v1 contains compact report and provenance data while excluding raw transcripts, canonical event bodies, source code, secrets, absolute private paths, and unnecessary identity metadata.
**Consequences:** Network transfer is explicit. New submission fields require privacy review, schema versioning, size limits, and tests.
**Related:** `PRIVACY.md`, `SECURITY.md`, `schemas/submission-v1.schema.json`, `src/loopmetry/submission.py`

## D-004 — Versioned JSON contracts; renderers do not own evaluation logic

**Status:** Accepted
**Context:** CLI, HTML, future web UI, and administrator services need a stable boundary without duplicating metric formulas.
**Decision:** JSON and JSON Schema are the integration contracts. Markdown, static HTML, and future React clients render those contracts and must not recompute metrics, submission identity, or review state.
**Consequences:** Contract-breaking changes require an explicit schema version and migration plan.
**Related:** `schemas/`, `src/loopmetry/report.py`, `docs/web-ui.md`

## D-005 — Use `uv`; keep the runtime standard-library-only by default

**Status:** Accepted
**Context:** Participant setup should be reproducible and low-friction, while the CLI and local server should remain portable.
**Decision:** `uv` manages Python versions, locking, commands, and builds. Runtime dependencies remain standard-library-only unless a dependency has a documented security, portability, and maintenance justification.
**Consequences:** Commit `uv.lock`; use `uv sync --locked`, `uv lock --check`, and `uv build` in verification. Do not add parallel dependency-management systems.
**Related:** `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`

## D-006 — Static HTML first; React only for multi-run interactive workflows

**Status:** Accepted
**Context:** A single project report is bounded and read-only; adding a second runtime and duplicated state too early would increase complexity.
**Decision:** Self-contained HTML is the default visualization. Add a React client only when timeline filtering, evidence-graph traversal, cohort comparison, live ingestion, or consent/payload-preview workflows require a persistent interactive UI.
**Consequences:** The future web client consumes versioned JSON/API contracts and never imports Python metric logic.
**Related:** `src/loopmetry/report.py`, `docs/web-ui.md`

## D-007 — One-command participant flow and administrator cohort collection are the current milestone

**Status:** Accepted
**Context:** Participants need a copy-paste command; administrators need visibility across registered submitters, including non-submitters and multiple attempts.
**Decision:** Prioritize: (1) one participant command that discovers or accepts input, evaluates, renders, packages, and optionally submits; and (2) administrator roster, receipt, attempt history, manual review status, and cohort dashboard/export.
**Consequences:** Local artifacts are written before upload, failed uploads are retryable, roster entries exist before submission, and participant ordering is independent of scores.
**Related:** `src/loopmetry/workflow.py`, `src/loopmetry/submission.py`, `src/loopmetry/admin_storage.py`, `src/loopmetry/admin_server.py`, `docs/submission-workflow.md`

## D-008 — Local LLM execution is deferred; preserve only extension contracts

**Status:** Accepted
**Context:** Local model runtimes add installation, capability, schema-following, resource, and support complexity. They are not required to validate the participant/admin workflow.
**Decision:** Keep bounded evaluation-bundle, rubric, provider, and result-schema boundaries, but do not invoke Claude, Codex, Ollama, LM Studio, or another model during participant analysis, submission, or administrator ingestion in the current milestone.
**Consequences:** LLM runtime implementation begins only after deterministic submission contracts and human calibration are stable.
**Related:** `src/loopmetry/llm_bundle.py`, `docs/llm-evaluation.md`, `rubrics/`, `schemas/llm-evaluation-*.json`

## D-009 — Submission retries are idempotent and review status is manual

**Status:** Accepted
**Context:** Network retries must not inflate attempt counts, and metric values must not automatically determine acceptance.
**Decision:** The server identifies identical submissions by content hash and returns the existing receipt. A genuinely new run creates a new attempt. Review states are assigned manually and retained as operational history.
**Consequences:** Token rotation, duplicate retry, attempt ordering, and status-history behavior require tests.
**Related:** `src/loopmetry/submission.py`, `src/loopmetry/admin_storage.py`, `docs/submission-workflow.md`

## D-010 — Keep agent instructions navigational; store rationale in this decision log

**Status:** Accepted
**Context:** Large agent instruction files become stale, duplicate task documents, and make future sessions expensive to orient. Historical rationale is also lost when instructions are rewritten in place.
**Decision:** `AGENTS.md` contains mission boundaries, source-of-truth routing, stable invariants, verification commands, and documentation discipline. Detailed design belongs in task documents; enduring rationale and direction changes belong in this log.
**Consequences:** Future agents start from `AGENTS.md`, use this log for “why,” and inspect code/tests for implementation truth. Superseded decisions remain visible.
**Related:** `AGENTS.md`, `docs/architecture.md`, `docs/roadmap.md`

## D-011 — Support both prospective hook capture and retrospective historical backfill

**Status:** Accepted
**Context:** Hook capture provides privacy-minimized, relatively format-stable forward collection, but it cannot recover work performed before integration. Historical backfill enables zero-setup analysis and recovery of existing Claude Code and Codex sessions, but depends on versioned vendor formats and requires explicit consent for local transcript access.
**Decision:** Hook capture and historical backfill are both first-class source adapters behind one shared `SourceAdapter` contract, and both produce the same canonical event schema. The default participant experience will eventually be a hybrid `loopmetry run --source auto` mode that merges approved hook and history evidence.
**Consequences:**

- neither source path may implement metric semantics;
- every imported event carries source, capture-mode, adapter-version, coverage, and diagnostic provenance (capture modes distinguish at least `hook`, `history-backfill`, `explicit-import`, and `deterministically-derived`);
- overlapping events are deduplicated without discarding provenance; conflicting observations are never silently resolved — they surface as `adapter_conflict` diagnostics and reduce confidence;
- home-directory history discovery is bounded, previewable, and consented; non-interactive runs never read history implicitly;
- raw transcripts are read locally in streaming fashion, are never copied wholesale, and remain excluded from submission v1; and
- hook integration is recommended for prospective capture quality but is not required for analysis.

**Related:** `docs/architecture.md`, `docs/hook-capture.md`, `docs/roadmap.md`, `docs/submission-workflow.md`, `src/loopmetry/hook_capture.py`, future `src/loopmetry/adapters/`

---

## Adding or changing a decision

Append a new entry with a stable ID. Include:

- **Status** and date;
- **Context** — the constraint or problem;
- **Decision** — the chosen direction;
- **Consequences** — required behavior and trade-offs; and
- **Related** paths — contracts, docs, code, and tests.

Do not delete accepted decisions. When direction changes, add a replacement entry and mark the prior one **Superseded** with a link to its replacement. Keep transient debugging notes, action-run failures, and incidents in issues or operational notes rather than this architectural log.
