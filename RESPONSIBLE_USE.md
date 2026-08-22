# Responsible use

Loopmetry is a project-evidence tool. It is not a validated instrument for measuring developer talent, productivity, employability, or organizational value.

## Intended uses

Appropriate early uses include:

- a developer reviewing their own agent-assisted project;
- a team retrospective with informed participant consent;
- checking whether specifications, tests, and commits are traceable;
- comparing two workflow configurations on the same controlled task;
- identifying missing verification or evidence coverage; and
- researching project-level human–agent collaboration with appropriate governance.

## High-risk and unsupported uses

Do not use Loopmetry as the sole or primary input for:

- hiring or candidate screening;
- termination, promotion, compensation, or disciplinary decisions;
- individual productivity leaderboards;
- covert employee surveillance;
- comparing people who worked on materially different projects;
- inferring intent, effort, expertise, or misconduct from missing events; or
- claiming that a metric is a scientifically validated measure before calibration evidence exists.

## Required human review

Every metric is conditional on adapter coverage and recorded evidence. Before acting on a report, a reviewer should verify:

1. which repositories and sessions were observed;
2. whether requirements and verification events were captured correctly;
3. whether work occurred outside the agent transcript;
4. whether project size and complexity make comparison meaningful;
5. whether the confidence and measurement-gap sections were considered; and
6. whether the affected people can inspect and contest the evidence.

## Steering is descriptive

Loopmetry does not grade intervention frequency. Experts may interrupt agents more often because they recognize risks earlier; other projects may benefit from long autonomous runs. The steering label describes an observed interaction pattern only.

## Data minimization

Adapters should produce the minimum evidence needed for project analysis. Raw prompts, source-code excerpts, customer data, credentials, personal email addresses, and repository URLs are not required by the core metric engine.

## Research and organizational deployment

Before deployment beyond personal use, establish:

- a lawful data-processing basis;
- participant notice and access rights;
- retention and deletion policies;
- security controls for the local evidence store;
- a process for correcting adapter errors;
- project-type calibration; and
- independent review of metric validity and unintended incentives.
