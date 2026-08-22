# Loopmetry

**Evidence-backed project evaluation and submission for human–agent software work.**

Loopmetry reconstructs how project intent became plans, file changes, verification evidence, recovery actions, and delivered results. It evaluates the **recorded project workflow**, not the developer, and does not produce a universal participant rank.

The current product has two operating surfaces:

1. a participant runs one command to analyze local normalized evidence, create a standalone HTML report, package a privacy-minimized submission, and optionally upload it; and
2. an administrator enrolls a roster, receives idempotent submissions, tracks missing and repeated attempts, assigns review status, and exports the cohort view.

## Participant: one-command analysis and submission

After Loopmetry capture hooks have written normalized events under the project’s `.loopmetry/hooks/` directory, the participant runs the one-line command issued by the administrator.

macOS, Linux, or WSL:

```bash
LOOPMETRY_SUBMISSION_TOKEN='<issued-token>' uvx --from git+https://github.com/GoGiants1/Loopmetry.git loopmetry run --assignment-id agent-ai-2026 --submitter-id S001 --server https://loopmetry.example.com
```

Windows PowerShell:

```powershell
$env:LOOPMETRY_SUBMISSION_TOKEN='<issued-token>'; uvx --from git+https://github.com/GoGiants1/Loopmetry.git loopmetry run --assignment-id agent-ai-2026 --submitter-id S001 --server https://loopmetry.example.com
```

`admin import-roster` writes both forms into the private participant credentials CSV, so participants do not need to assemble options themselves.

With Loopmetry already installed, the same workflow is:

```bash
LOOPMETRY_SUBMISSION_TOKEN='<issued-token>' \
loopmetry run \
  --assignment-id agent-ai-2026 \
  --submitter-id S001 \
  --server https://loopmetry.example.com
```

`loopmetry run` performs one bounded workflow:

```text
discover/merge normalized local events
        → deterministic evaluation
        → report.json + standalone report.html
        → content-addressed submission.json
        → optional authenticated upload
        → receipt.json
```

Artifacts remain under `.loopmetry/runs/<run-id>/`. If upload fails, analysis is not lost:

```bash
LOOPMETRY_SUBMISSION_TOKEN='<issued-token>' \
loopmetry submit .loopmetry/runs/<run-id>/submission.json \
  --server https://loopmetry.example.com
```

For an explicit normalized input instead of hook discovery:

```bash
loopmetry run \
  --input examples/demo_project.jsonl \
  --assignment-id agent-ai-2026 \
  --submitter-id S001
```

The following environment variables can replace repeated CLI options:

```text
LOOPMETRY_ASSIGNMENT_ID
LOOPMETRY_SUBMITTER_ID
LOOPMETRY_SERVER_URL
LOOPMETRY_SUBMISSION_TOKEN
```

## Administrator: roster, collection, and cohort dashboard

Create a roster CSV:

```csv
submitter_id,display_name
S001,Alice
S002,Bob
```

Enroll the roster and generate one-time participant tokens. Only token hashes remain in the administrator database; the credentials CSV is written with private file permissions.

```bash
uv run loopmetry admin import-roster roster.csv \
  --assignment-id agent-ai-2026 \
  --db .loopmetry/admin.db \
  --server https://loopmetry.example.com \
  --output .loopmetry/participant-credentials.csv
```

Start the collection API and HTML dashboard:

```bash
LOOPMETRY_ADMIN_TOKEN='replace-with-a-long-random-secret' \
uv run loopmetry admin serve \
  --db .loopmetry/admin.db \
  --bind 127.0.0.1 \
  --port 8787
```

Open `http://127.0.0.1:8787` and authenticate with username `admin` and the value of `LOOPMETRY_ADMIN_TOKEN`.

The dashboard shows the full roster, including participants who have not submitted, the latest attempt for each participant, attempt history, manual review state, metric confidence, measurement gaps, and submission detail. It deliberately orders by roster identity rather than by score.

Manual review states are:

```text
received → reviewing → accepted
                     ↘ needs_revision
```

Useful administrator commands:

```bash
uv run loopmetry admin list --assignment-id agent-ai-2026
uv run loopmetry admin set-status <submission-id> needs_revision --note "Add test evidence"
uv run loopmetry admin export --assignment-id agent-ai-2026 --output cohort.csv
```

The built-in HTTP server binds to loopback by default. For participant uploads over a network, put it behind an HTTPS reverse proxy and explicit access control. The participant CLI refuses to send enrollment tokens over plaintext HTTP to a non-loopback host.

## What is submitted

The v1 submission envelope contains:

- assignment, roster, project, run, and client identifiers;
- event/session/source counts and the observed time window;
- deterministic metric cards;
- evidence references and privacy-minimized summaries;
- metric confidence and measurement gaps; and
- explicit privacy declarations.

It does **not** contain:

- raw Claude Code or Codex transcripts;
- raw prompts or complete model responses;
- canonical event records;
- source-code bodies or full diffs;
- absolute source paths; or
- API keys, Git author email, or remote repository URLs.

The submission is content-addressed. Retrying the same `submission.json` is idempotent; a newly generated run becomes a new attempt.

See [`schemas/submission-v1.schema.json`](schemas/submission-v1.schema.json), [`PRIVACY.md`](PRIVACY.md), and [`SECURITY.md`](SECURITY.md).

## Capture local Claude Code and Codex evidence

Loopmetry accepts official lifecycle-hook payloads and writes privacy-minimized canonical events:

```bash
loopmetry capture-hook --source claude-code --project-id my-project
loopmetry capture-hook --source codex --project-id my-project
```

The hook command reads one JSON payload from standard input, removes raw prompt/source/command bodies, and appends normalized events under `<project>/.loopmetry/hooks/`. Hook installation is currently explicit so administrators can review the exact configuration diff. Historical transcript backfill and automated integration setup remain on the roadmap.

See [`docs/hook-capture.md`](docs/hook-capture.md).

## Deterministic project metrics

The core reports four independent metric cards:

- **Intent & Evidence Traceability** — requirements → plans → changes → verification → commits
- **Verification Rigor** — whether changed work is followed by relevant and successful checks
- **Recovery Efficiency** — whether recorded failures are resolved through a convergent loop
- **Change Discipline** — requirement linkage, edit convergence, revert avoidance, and delivery completion

Human steering is reported only as a non-scored workflow signal. Every metric includes component values, canonical evidence IDs, confidence, and explicit gaps. Missing evidence is not silently interpreted as success.

## Local reports and future web client

```bash
uv run loopmetry analyze examples/demo_project.jsonl --format html --output report.html
```

The HTML report is self-contained: inline CSS, no CDN, no analytics, no remote fonts, and no network requests. The administrator dashboard is also server-rendered HTML over versioned JSON data. A React client can be added later for large evidence graphs, comparison workflows, and richer filtering without moving metric logic into the browser.

See [`docs/web-ui.md`](docs/web-ui.md).

## LLM evaluation extension point

LLM evaluation is deliberately **not** on the current critical path. Loopmetry retains a bounded bundle contract and versioned rubric/schema so remote or on-device judge providers can be added later without changing deterministic metrics or the submission API:

```bash
uv run loopmetry bundle examples/demo_project.jsonl \
  --output .loopmetry/demo.llm-eval-bundle.json
```

No local LLM runtime or automatic judge invocation is implemented in this milestone. See [`docs/llm-evaluation.md`](docs/llm-evaluation.md).

## Development with uv

Requirements: `uv` and Python 3.11 or newer.

```bash
git clone https://github.com/GoGiants1/Loopmetry.git
cd Loopmetry
uv sync --locked
uv run python -m unittest discover -s tests -v
uv run loopmetry validate examples/demo_project.jsonl
uv run loopmetry run --input examples/demo_project.jsonl \
  --assignment-id demo --submitter-id local
uv build
```

The runtime remains Python-standard-library-only.

## Responsible use

Loopmetry is intended for project retrospectives, training submissions, self-review, evidence-completeness review, and controlled workflow comparisons. It is not designed for automated hiring, termination, compensation, promotion, covert monitoring, or employee ranking. See [`RESPONSIBLE_USE.md`](RESPONSIBLE_USE.md).

## License

Apache License 2.0. See [`LICENSE`](LICENSE).
