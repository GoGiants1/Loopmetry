# Provenance and clean-room development

Loopmetry is an independent implementation of a general project-level workflow evaluation concept.

## Inputs used to define the product scope

The initial scope was derived from publicly observable problem statements in the coding-agent ecosystem: local agent transcripts contain evidence about requirements, plans, tool use, code changes, verification, failures, human interventions, and delivery. The implementation translates those general observations into an original canonical event schema and original metric definitions.

## Deliberate exclusions

The project does not use or seek to reproduce:

- proprietary source code, container images, private APIs, or unpublished scoring formulas;
- competitor output sampling for formula extraction or parity testing;
- competitor-specific archetype names, visual designs, report copy, or metric weights; or
- a universal developer or employability score.

No proprietary product output is used as a test oracle. Tests use synthetic events created for this repository.

## Third-party code

The initial implementation was written independently and contains no copied third-party source files. Future contributions that incorporate third-party code must preserve the applicable notices and update `THIRD_PARTY_LICENSES.md`.

## Metric provenance

The v0.1 metrics are documented in `docs/metrics.md`. Every formula, threshold, and limitation is visible in the repository and implemented in `src/loopmetry/evaluation.py`.
