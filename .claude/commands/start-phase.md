---
description: Build one phase from its spec, then run its acceptance checks
argument-hint: <path to spec, e.g. docs/specs/phase01-lean-dashboard.md>
---

Build the phase described in $ARGUMENTS.

1. Read CLAUDE.md, the spec, and every file the spec's Context section links to.
2. Before writing code, list any contradiction between the spec, PRODUCT.md, ARCHITECTURE.md boundaries and docs/decisions/. If there is one, stop and ask me.
3. Build only what is In Scope. Respect Out of Scope and Constraints. Update site/docs/ in the same change when behavior or API changes.
4. When ambiguous, ask "A or B? I recommend B because ..." and wait.
5. Run the repo checks: `uv run --with ruff ruff check src`, `uv run --with 'mypy<2' --with types-PyYAML mypy`, `uv run pytest -q -n auto -m "not integration"`, and `npm run build` in any frontend you touched.
6. Run every acceptance check and report each one as pass or fail with the measured number.
7. Do not update docs/progress.md or commit. I will run /close-phase after my manual check.
