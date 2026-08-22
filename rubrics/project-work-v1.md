# Project Work Evaluation Rubric v1

Evaluate only the evidence included in the Loopmetry evaluation bundle. Do not infer actions, quality, intent, or outcomes that are not supported by an evidence ID.

## Dimensions

1. **Goal fidelity** — whether implemented changes and delivered behavior match the recorded requirement and acceptance criteria.
2. **Evidence sufficiency** — whether the available evidence is adequate to justify the claimed completion state.
3. **Verification quality** — whether checks are relevant, broad enough for the change, and interpreted correctly.
4. **Recovery reasoning** — whether failures are diagnosed and resolved through a convergent, evidence-driven loop.
5. **Scope and risk control** — whether the work avoids unrelated change and identifies material security, privacy, operational, and maintenance risks.

## Rating scale

- `0`: evidence directly contradicts the criterion or shows a material failure.
- `1`: weak evidence; major gaps or unresolved problems dominate.
- `2`: mixed or partial evidence; the criterion is only partly satisfied.
- `3`: strong evidence with limited, non-material gaps.
- `4`: unusually complete, internally consistent evidence with no material gap found.
- `null`: not assessable from the supplied evidence.

Ratings are ordinal judgments, not percentages. Do not calculate or return an overall numeric score.

## Required behavior

- Cite canonical evidence IDs for every substantive judgment.
- List counterevidence separately from supporting evidence.
- Mark missing evidence explicitly.
- Use `indeterminate` when the evidence cannot support a completion judgment.
- Set `needs_human_review` when evidence conflicts, risk is high, or the outcome depends on domain judgment.
- Evaluate the project workflow, not the skill, personality, or employability of any person.
- Do not quote raw prompts, source code, secrets, personal information, or private paths even when present in the bundle.
