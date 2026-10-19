# AGENTS.md

Guidance for coding agents (Codex, Cursor, Claude Code and others) working in this repository.

All rules live in [CLAUDE.md](CLAUDE.md), so the two files cannot drift. Read it first, then the files it points to:

- [PRODUCT.md](PRODUCT.md): who VoiceGateway is for, what it does and what it will not do.
- [ARCHITECTURE.md](ARCHITECTURE.md): components, data flow, boundaries and stack.
- [DESIGN.md](DESIGN.md): the visual system, for UI work.
- [docs/decisions/](docs/decisions/), [docs/specs/](docs/specs/) and [docs/progress.md](docs/progress.md): why, what next, and where the project stands.

Quick commands:

```bash
make help    # list the targets
make up      # start the six-service stack
make test    # Go tests with -race and the dashboard tests
make lint    # Go lint, dashboard lint and TypeScript checks
```
