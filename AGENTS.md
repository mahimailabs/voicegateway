# AGENTS.md

Guidance for coding agents (Codex, Cursor, Claude Code and others) working in this repository.

All rules live in [CLAUDE.md](CLAUDE.md), so the two files cannot drift. Read it first, then the files it points to:

- [PRODUCT.md](PRODUCT.md): who VoiceGateway is for, what it does and what it will not do.
- [ARCHITECTURE.md](ARCHITECTURE.md): components, data flow, boundaries and stack.
- [DESIGN.md](DESIGN.md): the visual system, for UI work.
- [docs/decisions/](docs/decisions/), [docs/specs/](docs/specs/) and [docs/progress.md](docs/progress.md): why, what next, and where the project stands.

Quick commands:

```bash
uv sync --extra dev --extra dashboard            # install
uv run pytest -q -n auto -m "not integration"    # unit tests
uv run --with ruff ruff check src                # lint
```
