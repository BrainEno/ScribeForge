# TDD Policy

TDD is a repository requirement, not a preference.

## Cycle
**RED:** add the smallest test describing one behavior; execute it and observe the intended failure.
**GREEN:** implement only enough production code to pass.
**REFACTOR:** improve names/design without changing behavior; keep tests green.

For external OCR engines, tests target our adapter contract using recorded/local fixtures. Default CI must be deterministic, offline, and must not download multi-GB models.

## Required checks
```bash
pytest
ruff check .
mypy src
```

Coverage is initially enforced at 90% for the small core. The threshold may only change in a dedicated, justified change; never lower it opportunistically.

Markers:
- `integration`: local deterministic integration tests
- `benchmark`: real OCR benchmark corpus
- `gpu`: requires supported GPU/runtime

Default CI excludes benchmark/GPU tests.
