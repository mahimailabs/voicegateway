# CLAUDE.md

## Project

VoiceGateway: a self-hosted platform that profiles LiveKit voice agents end to end, one view per call, from the phone line to the model.

## Source of truth

Read these before any feature work. Do not restate their contents here.

- PRODUCT.md: who it is for, what it does, what is out of scope.
- ARCHITECTURE.md: components, boundaries, stack and the reasons for it.
- DESIGN.md: visual system. Only for UI work.
- docs/decisions/: why each major choice was made. Never reverse one without writing a new decision.
- docs/specs/: one file per phase. The current phase is the one named in the prompt.
- docs/progress.md: what is done, what is next, open questions.

## Stack

Go 1.26 (one binary, `voicegateway`), Postgres 16, ClickHouse 26.1, Redis 8, MinIO. Dashboard: React 19, TypeScript 5, Vite, Tailwind v4, shadcn/ui. SDK: Python 3.11 to 3.13. Exact versions live in go.mod, package.json, uv.lock and deploy/compose.yaml.

## Commands

Every task goes through the root Makefile.

- Run locally: `make dev` (compose stack, web, worker, Vite dev server)
- Test: `make test` (Go with `-race`, web, SDK); `make e2e` for test/
- Lint and format: `make lint`; pre-commit runs the same on commit

## Boundaries

- web never writes to ClickHouse; worker never answers HTTP. The Redis stream is their only contract.
- Flat packages under `internal/`. Share only `event`, `stream`, `store`. No `utils/`, `common/`, `shared/`, `pkg/`.
- Interfaces live in the package that uses them. Wiring is constructor injection in `internal/app` only.
- The dashboard reads only through web's API, never a database.

## Code quality

- Every commit passes the pre-commit hooks. Go: gofumpt, goimports, golangci-lint (vet, staticcheck, errcheck, gosec), `go mod tidy` clean. `sdk/python`: ruff, ruff format, mypy. `dashboard/`: eslint, prettier, `tsc --noEmit`.
- Tests prove behaviour; coverage is not the goal. One test per rule that matters (a join, an ack after insert, unknown is not zero). Table-driven, run with `-race`.
- Run the real thing instead of mocking it: Redis, ClickHouse and Postgres through testcontainers-go, webhooks from `livekit-server --dev`. Use `testing/synctest` for timers and batch flushes.
- Frontend: Vitest with Testing Library, Playwright for user flows. Python SDK: pytest with LiveKit Agents' own test helpers (testing-livekit-agents skill).

## Before writing code

- Use the framework's own way first. LiveKit tokens, webhooks, egress and SIP types: livekit skills and the livekit-docs MCP. ClickHouse and Redis: their skills. Go libraries: Context7. Our own code: gopls MCP.
- UI: compose screens from pre-built components (shadcn/ui, Magic UI and other shadcn registries, via the shadcn and magicui MCPs). No hand-written component markup, CSS or SVG when a library component exists.
- Lists and their filters use `src/components/data-table.tsx` (TanStack Table through ReUI's data grid, ReUI filter chips). A filter field's `get` returns null for unknown, which matches only "is empty". Files under `src/components/reui/` are vendored; keep local edits minimal.
- Page state someone would want to share (tabs, ranges, filters, the open drawer, the selected item) goes in the URL with `useUrlState` or `useUrlPick` from `src/lib/url-state.ts`, never in `useState`. Links stay scope-free (`/calls`); the router adds `/org/<id>/project/<id>`.
- Build from scratch only when no library method exists, and say so in the PR.
- When we adopt a framework with an official skill or docs MCP, install it at project scope. If it cannot be installed, ask me.

## Git identity

- New branches use `feat/` or `fix/` prefixes, as requested by the maintainer.

- Author and commit as `mahimairaja <mahimairaja3@gmail.com>`. Use the `mahimairaja` account for GitHub comments, pull requests and other writes. Verify the active account before writing; never add a bot or agent co-author.

## Versions

- Pin exact versions: the `go` and `toolchain` lines, dev tools (golangci-lint, sqlc, goose, gofumpt) as `tool` directives in go.mod, images by tag and digest, npm with save-exact and the lockfile, Python dev tools in uv.lock.
- Exception: the published Python SDK declares compatible ranges for runtime dependencies so it does not break users' installs. Its lockfile stays exact.

## Never do

- Never commit secrets or .env files.
- Never invent data, API responses or test results. If something is unknown, say so.
- Never mark a phase done until its acceptance checks pass.
- Never change behaviour outside the current spec without asking first.
- Never show a missing value as zero. Unknown stays unknown; inferred values say so.
- Never log raw webhook or request bodies.
- Never panic in the pipeline; return wrapped errors.

## How to ask me

- When a choice is ambiguous, ask: "A or B? I recommend B because ...".
- Ask one question at a time and wait for the answer.
- If the spec contradicts PRODUCT.md or a decision record, stop and point at the contradiction.

## Working loop

1. Read the spec for this phase, plus the files above.
2. Build only what the spec asks for.
3. Run the acceptance checks in the spec and report each result with its number.
4. Update docs/progress.md. Add any new lasting rule to this file.
