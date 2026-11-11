# Contributing to ScribeForge

Please read [AGENTS.md](AGENTS.md) first — it is the authoritative rule set for
this repository (product invariant, mandatory TDD, test boundaries, and
architecture constraints all live there).

In short:

1. **TDD is mandatory.** Write or update a test that expresses the behavior,
   watch it fail for the expected reason, then implement the smallest change
   that passes. Bug fixes need a regression test first. Never weaken or delete
   a valid test just to make CI green.
2. **Keep the evidence invariant.** Never silently "improve" source text. OCR,
   normalization, verification, and editorial text are separate layers, and
   every accepted decision must stay traceable to source page coordinates and
   engine evidence.
3. **One open issue per PR.** Pick an open issue (or open one first), work on
   a dedicated branch, and open a PR that closes it. CI must be fully green
   before merge.
4. **Benchmarks and GPU/model tests are opt-in markers** and must never block
   ordinary contributor CI.
5. **Run the checks locally before pushing:** `pytest`, `ruff check .`,
   `ruff format --check .`, `mypy src` (see the root README for bootstrap).

License: all contributions are accepted under the Apache-2.0 license of the
project — see [LICENSE](LICENSE).
