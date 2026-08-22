# Contributing

## Development setup

```bash
git clone https://github.com/GoGiants1/Loopmetry.git
cd Loopmetry
python -m pip install -e .
python -m unittest discover -s tests -v
```

Runtime code should remain standard-library-only unless a dependency has a clear security, maintenance, and portability justification.

## Design requirements

Contributions to metrics or adapters should follow these rules:

1. Separate source parsing from metric computation.
2. Preserve an evidence path from every interpretation to canonical event IDs.
3. Emit confidence and measurement gaps.
4. Do not add a hidden or universal developer score.
5. Add adversarial and missing-evidence tests.
6. Document false positives, false negatives, and unsupported source behavior.
7. Avoid raw prompt or source-code storage when normalized evidence is sufficient.

## Adding an event type

Before expanding the canonical schema, document:

- why existing types cannot represent the evidence;
- required and optional data fields;
- privacy implications;
- which adapters can emit it reliably; and
- how old persisted data will be migrated.

## Adding a metric

A metric proposal must include:

- a plain-language construct definition;
- exact deterministic formula and weights;
- required evidence types;
- confidence calculation;
- known confounders;
- counterexamples;
- tests for missing and adversarial evidence; and
- a statement of unsupported uses.

## Pull requests

Keep changes focused and include tests. For user-visible changes, update the relevant document in `docs/` and the example report workflow when applicable.
