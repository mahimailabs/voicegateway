# CLAUDE.md

<!--
Rules every coding agent reads at the start of a session. AGENTS.md points here.
Keep this file under 80 lines. Facts live in PRODUCT.md, ARCHITECTURE.md and DESIGN.md; keep only rules and pointers here.
-->

## Project

VoiceGateway is the open-source profiler for voice agents: one `attach()` line prices and times every STT, LLM and TTS call in a LiveKit, Pipecat or OpenRTC agent, joined with the SIP, SFU and node layers below it.

## Source of truth

Read these before feature work. Do not restate them here.

- PRODUCT.md: users, purpose, out of scope, failure modes, principles.
- ARCHITECTURE.md: components, data flow, boundaries, stack, data model.
- DESIGN.md: visual system. Only for UI work.
- docs/decisions/: why each major choice was made. Never reverse one without writing a new record.
- docs/specs/: one spec per phase or design. The current one is named in the prompt.
- docs/progress.md: what is done, what is next, open questions.
- contributing/: setup, testing, code style, adding a provider, refreshing pricing.

## Stack

Python 3.11 to 3.13 (async throughout), FastAPI, SQLModel and SQLAlchemy 2 with Alembic, SQLite by default. React 18, TypeScript 5 and Vite 5 dashboard. Fumadocs docs and Astro landing page on Cloudflare. Details in ARCHITECTURE.md.

## Commands

- Install: `uv sync --extra dev --extra dashboard` (or `pip install -e ".[dev]"`)
- Test: `uv run pytest -q -n auto -m "not integration"`; one test: `pytest path/to/test_file.py::test_name`
- Lint and types: `uv run --with ruff ruff check src` and `uv run --with 'mypy<2' --with types-PyYAML mypy`
- Run: `voicegw init && voicegw serve --port 8080`; MCP server: `voicegw mcp`
- Dashboard: `cd src/dashboard/frontend && npm install && npm run dev` (or `npm run build`)
- Docs placeholders: `tools/scripts/check-placeholders.sh`

## Boundaries

- `import voicegateway` never imports LiveKit or Pipecat. Frameworks load lazily inside `attach()` and `guard()`.
- The agent side (`attach`, `guard`, sinks) never imports the server or dependency-injector.
- Prices come only from voice-prices or the user's rate card. Never hardcode a price.
- Dashboard reads go in `server/api/dashboard/` (`/api/*`, `require_principal`). Writes and ingest go in `/v1/*`.
- Every model change ships with an Alembic migration in the same PR.
- Capture fails open: metering errors are logged, never raised into the agent's call.
- Unknown is not zero. Do not store or render a value that is not observed.
- Commercial code lives only under `ee/`. The MIT core must work without it.
- Install provider plugins in the user's agent, never as VoiceGateway extras.
- Do not move files in `examples/`, the root `docker-compose*.yml`, `install.sh` or `collector.sh`: published URLs point at them.

## Conventions

- Tests: pytest with `asyncio_mode = "auto"`, so no `@pytest.mark.asyncio`. `src/voicegateway/tests/conftest.py` sets fake provider keys.
- Config is YAML at `voicegw.yaml` with `${VAR}` env substitution.
- Docs version with the code: change `site/docs/` in the same PR as any behavior or API change.
- Writing voice: short declarative sentences, sentence-case headings, no em dashes (CI fails on one in the sites).
- Branches start with `feat/`, `fix/` or `chore/`. Commits follow Conventional Commits.

## Never do

- Never commit secrets, `.env` files or a real `voicegw.yaml`.
- Never invent prices, benchmarks, test results or customer claims. If something is unknown, say so.
- Never skip, disable or quarantine a test to get CI green.
- Never add AI attribution (Co-Authored-By trailers, "Generated with" lines) to commits, PRs or code.
- Never write the word "think" in code comments or commit messages; state what the code does.
- Never mark a phase done until its acceptance checks pass.
- Never change behavior outside the current spec without asking first.

## How to ask

- When a choice is ambiguous, ask: "A or B? I recommend B because ...".
- Ask one question at a time.
- If a spec contradicts PRODUCT.md or a decision record, stop and point at the contradiction.

## Working loop

1. `/new-spec NN name` drafts `docs/specs/phaseNN-name.md` from `docs/specs/_template.md`.
2. `/start-phase <spec>` builds only what the spec asks for and reports every acceptance check with its number.
3. Try it by hand, then `/close-phase <spec>` updates docs/progress.md, commits and opens a PR.
4. Add any new lasting rule here and any lasting choice to docs/decisions/.
