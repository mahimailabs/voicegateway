# Phase 01: Console, UI first

## Goal

A developer opens the VoiceGateway console, sees every page of the product, and can browse a list of calls and open one to read its whole trace: transcript, turns, per-step latency, usage and agent spans. The data is real captured LiveKit traffic served by a mock API, and the screens define the API contract the backend will later serve.

## Context

- PRODUCT.md: V1 capabilities 1 (call trace) and 3 (live view, shell only); principles 2, 5 and 7.
- ARCHITECTURE.md: Repository Layout (`dashboard/src/features/<page>/`), Boundaries (the dashboard reads only through web's API).
- DESIGN.md: The Clean Room, all of it.
- Decisions: 0010 (usage, not prices), 0011 (dashboard served from files), 0012 (call identity).
- Go spike findings: spans carry no room sid, only `job_entrypoint` and `agent_session` carry `lk.room_name`, SIP shows up as participant kind SIP with `sip.*` attributes.

## In Scope

- A new app in `dashboard/`: React 19, TypeScript strict, Vite, Tailwind v4 themed with DESIGN.md tokens, shadcn/ui and Magic UI components, TanStack Router and Query, light and dark themes.
- The console shell: sidebar navigation with Calls, SFU, SIP, Live and Settings, plus the call detail route.
- Calls list: every call with room, start time, duration, participants (agent, caller, SIP), end state (observed or inferred) and a turn count. Search by room and a time-range filter.
- Call detail: header (room, times, participants, end state), the transcript by turn, per-turn STT, LLM, TTS and end-of-turn latency, usage per turn and per call (tokens, audio seconds, characters), and the agent spans on a timeline.
- SFU, SIP, Live and Settings pages as designed empty states that say what will appear there and in which phase.
- The API contract in `docs/api/openapi.yaml`, extracted from the two built screens. Every property carries an `x-source` naming where it comes from (webhook event, span name and attribute, SDK record, or derived).
- TypeScript types generated from the contract. The console imports only generated types for API data.
- A mock API with MSW serving fixtures through the contract.
- Fixtures captured from a real local session: `livekit-server --dev`, one LiveKit Agents session, webhooks and OTLP spans, converted to contract shape. TURN passwords and keys are removed before they are committed.
- Embeddable widgets (decision 0023): answer time, calls, one call, turn anatomy and issues at `/org/<id>/project/<id>/embed/<widget>`, without the console shell, with "Powered by VoiceGateway", plus Settings, Embeds with a preview and a snippet. Tokens and allowed origins wait for Phase 2.
- Deep links: every page under `/org/<id>/project/<id>/`, with shareable page state in the query string.
- Makefile targets for the web app: `make dev`, `make test`, `make lint` (web parts only in this phase).

## Out of Scope

- Any Go code. The backend starts in Phase 2.
- Removing the old Python code (Phase 0).
- SFU, SIP and live data views beyond their empty states.
- Recording capture and playback of real audio (0008): the console shows the recording panel with its turn regions and the recordings-off state only. Prices or cost (0010), MCP.
- The docs and landing site in `site/`.

## Constraints

- Pre-built components only (CLAUDE.md, DESIGN.md). No hand-written component markup, CSS or SVG when a library component exists. The span timeline is the shadcn Chart component with horizontal range bars (each span from start to end on a shared millisecond axis). Gauges come from the @gauge-ui registry. Any other visual need: search the registries first; if nothing fits, stop and ask.
- No invented data. Every value on screen traces to a fixture, and every fixture traces to a captured event. A field nothing produces is shown as unknown.
- Exact versions pinned in `dashboard/package.json` with save-exact and a committed lockfile.
- Use the shadcn and magicui MCPs to find components, and Context7 for library APIs.

## Acceptance Checks

1. Given `make dev`, when the console opens, then all 6 routes (Calls, Call detail, SFU, SIP, Live, Settings) render with 0 console errors in both themes (measured by Playwright `shell.spec`, 12 of 12 page and theme runs).
2. Given the fixture set of N captured calls, when the Calls list loads, then it shows N rows, and each row's room, start, duration and participant count equal the fixture values (measured by the Vitest test `calls-list.test`, N of N rows).
3. Given a captured call, when its detail page opens, then every turn, latency and usage value on screen equals the fixture (measured by the Vitest test `call-detail.test`, 100% of rendered values matched).
4. Given a fixture turn with no reported TTS usage, when it renders, then the cell reads "Unknown" and never 0 (measured by the Vitest test `unknown-not-zero.test`, 1 of 1).
5. Given `docs/api/openapi.yaml`, when it is linted, then it has 0 errors, and 100% of schema properties carry an `x-source` (measured by `redocly lint` plus `dashboard/scripts/check-sources.ts`).
6. Given the generated types, when `tsc --noEmit` runs, then it passes with 0 errors, and no file under `dashboard/src/features/` declares its own API data type (measured by `make lint`).
7. Given the built pages, when axe runs on the Calls list and Call detail in light and dark, then there are 0 serious or critical violations (measured by Playwright with axe, 4 of 4 runs).
8. Given `dashboard/src/features/`, when it is searched for an `<svg` element or a `.css` file, then there are 0 hand-written ones (measured by `rg`, 0 matches).
9. Given the committed fixtures, when they are searched for `turnPassword`, API keys or secrets, then there are 0 matches (measured by `rg` and gitleaks, 0 findings).
10. Given a call with 200 turns, when its detail page opens, then it is interactive in under 1 second (measured by Playwright trace, 5 of 5 runs).

## Manual Check

Open the console in Safari, light then dark, and read one real call from the top of the transcript to its last span. It should feel like The Clean Room: quiet, one teal accent, nothing that needs explaining.
