# Metric definitions v0.1

Loopmetry metric values are deterministic functions of normalized project events. A metric is not a universal quality judgment. Each result includes a confidence value, evidence references, and gaps.

No overall project or developer rank is produced.

## 1. Intent & Evidence Traceability

Purpose: determine whether the recorded workflow forms an inspectable chain from intent to delivery.

| Component | Weight | Definition |
|---|---:|---|
| Planning coverage | 25% | Share of sessions with file changes that contain a plan before the first change |
| Requirement linkage | 30% | Share of unique changed files whose change events carry requirement IDs |
| Verification linkage | 30% | Share of requirement IDs attached to changes that also appear on successful verification events |
| Delivery linkage | 15% | Share of commits preceded, in the same session, by successful verification after the last change |

Primary limitations:

- adapters may not be able to infer requirement IDs from free-form transcripts;
- commits can occur outside the observed session;
- a plan event shows recorded planning, not necessarily plan quality.

## 2. Verification Rigor

Purpose: determine whether changes are followed by observable checks and end in a verified state.

| Component | Weight | Definition |
|---|---:|---|
| Post-change coverage | 35% | Share of changed sessions with a later verification event |
| Verification success rate | 25% | Passed checks divided by passed, failed, or errored checks |
| Verification breadth | 20% | Number of distinct verification kinds, with two kinds meeting the v0.1 target |
| Final verified state | 20% | Share of changed sessions whose last post-change verification passed |

Breadth is a context-sensitive signal. A small library may only need tests and type checks, while a deployment project may need build, security, and integration evidence. Future versions will support project-specific verification policies.

## 3. Recovery Efficiency

Purpose: determine whether recorded failures are resolved and whether recovery loops converge.

| Component | Weight | Definition |
|---|---:|---|
| Resolution rate | 55% | Share of explicit error events followed in the same session by a successful command or passed verification |
| Retry efficiency | 30% | Average `1 / (1 + failed retries before recovery)` across resolved errors |
| Repeat avoidance | 15% | Number of unique normalized error signatures divided by total error events |

When no explicit error events are recorded, the displayed score is provisionally 100 but confidence is only 0.35. Absence of error evidence is not proof of error-free execution.

## 4. Change Discipline

Purpose: characterize whether the change surface remains linked, convergent, and deliverable without treating normal iteration as failure.

| Component | Weight | Definition |
|---|---:|---|
| Requirement linkage | 35% | Share of file-change events carrying requirement IDs |
| Edit convergence | 30% | `min(2 × unique changed paths / file-change events, 1)`; up to two edits per file meets the v0.1 target |
| Revert avoidance | 20% | One minus the share of change events marked `revert`, `undo`, or `rollback` |
| Delivery completion | 15% | Share of changed sessions containing a commit event |

This metric does not claim that fewer edits are always better. Iterative investigation can be appropriate. The convergence component only flags unusually repeated modification relative to the observed change surface.

## Non-scored steering signal

Human intervention is classified descriptively as one of:

- `minimal-recorded-intervention`;
- `light-touch`;
- `checkpoint-driven`;
- `corrective`; or
- `interactive`.

The classification uses intervention density and action categories. It is never included in a quality score because both high-autonomy and high-interaction workflows can be effective.

## Confidence

Confidence reflects evidence availability, not statistical certainty. For example, traceability confidence increases when changes, requirement links, successful verifications, and commits are all present.

A high score with low confidence should be interpreted as “the available evidence looks favorable, but coverage is weak.”

## Planned calibration

Before declaring any metric suitable for organizational comparison, Loopmetry needs:

1. a synthetic adversarial test suite;
2. annotated real projects with consent;
3. inter-rater analysis for human-evaluation labels;
4. project-volume and project-type stratification;
5. sensitivity analysis for each threshold and weight; and
6. documented failure cases.
