# Progress

<!--
Updated at the end of every phase, by you or by /close-phase.
A fresh session reads this to know where the project stands.
-->

## Current Phase

Phase 1: the console, UI first. Spec: docs/specs/phase01-console.md.

## Done

| Phase | Spec | Result | Date |
| --- | --- | --- | --- |
| Setup | none | PRODUCT.md, ARCHITECTURE.md, DESIGN.md (The Clean Room seed), CLAUDE.md and decisions 0005 to 0013 written for the Go platform | 2026-10-12 |
| Go spike | none | Four gates passed: ingest (0 lost in 12 runs), LiveKit webhooks in Go, Agents spans joined to calls, ocb collector build. Results in decision 0009 | 2026-10-11 |

## In progress (Phase 1)

- Public presentation (2026-10-15): README now leads with the current wordmark, an 18-second console teaser, and a hand-drawn call-path diagram. The teaser uses the supplied console screenshot with editorial callouts, explicitly labelled as a preview. Only the finished SVG, GIF and still image are published; media authoring workspaces stay local and gitignored. The companion `voicegateway-web` landing adopts the Clean Room theme, interactive React previews with captured data, and the OSS/Cloud V1 split with automated Stripe activation. Console build/lint and landing build/typecheck/changed-source lint pass; this does not mark Phase 1 or Cloud complete.
- Console identity: selected Mergepoint concept 02 and Inter Semibold lettering form a single outlined wordmark, with light/dark SVGs in the existing ink and logo-only Gateway Teal. Typography was tuned to the system UI at 16px/600 after reviewing the earlier heavier geometric treatment. A brief electric sweep runs on entry and hover, with a static reduced-motion version. The tab icon and title match; the collapsed rail keeps its toggle. Artwork renders are checked at actual size; live browser verification is pending because localhost access is blocked by a saved browser permission.
- Alerts (decision 0020, PRODUCT.md updated): a top-level page with seven rules built on the existing checks, each evaluated on the captured data (17 would have fired), channels picked per rule. Slack, Microsoft Teams, Discord, PagerDuty, Opsgenie, email and webhook sit under Settings, Integrations; they connect on the server in Phase 2 so no secret is kept in the browser.
- Settings: a modal (sidebar-in-dialog) with eight OSS sections. Thresholds, tags and appearance work and are kept in the browser; the rest shows the captured project or "Not set" until Phase 2. One answer budget now drives every page (it was 1.5 s on Calls and 0.8 s on Overview). The OSS, Enterprise and Cloud line is decision 0017; Enterprise settings show as locked rows (0018); inviting people with organisation roles is open source (0019). Changes to tags and thresholds can be undone from a toast.
- Overview: a one-line verdict and one tile per page (Calls, Alerts, Agent workers, Media server, Phone bridge, Egress, Ingress), each computed by the same model and checks as the page it opens. Failures from our own tests are labelled and kept out of the verdict. The model, worker and media-server layers stay below; the phone-bridge layer now links to the SIP page instead of saying no bridge is connected. With this, every Phase 1 page is built from captured data; nothing is committed yet.
- Issues page and nav regroup (decision 0021): 22 issues from the captured data, grouped from the call findings and the agent and phone-bridge checks, each row opening to the calls or components it was found on. Nav: Overview; Observe (Live, Calls, Issues, Alerts); Agents; LiveKit (Media server, Phone (SIP), Egress, Ingress). The project page opens from the project switcher. Settings follows the ShipVoice Pro layout: a project picker in the modal sidebar, and Access (People, API keys, MCP server, Audit log locked as Enterprise) under Organisation.
- Tables: Calls, Issues, Alerts (rules and history), Agent workers, Egress and Ingress share one DataTable (TanStack Table 9 via ReUI's data grid, ReUI filter chips): sortable columns, expandable rows, paging past 25 rows, and filters such as "Answer time > 1500" or "Issues is High". Calls' stat cards follow the filters. Unknown values match only "is empty". The vendored ReUI code was patched to pass strict type checks: process.env read as import.meta.env, unused names removed. The call drawer's event log keeps its own compact table.
- Agent workers: a row opens an agent drawer: calls at once (counted from the agent's own time in each watched room, one chart per watch, retried joins included), CPU and memory against requests and limits, replicas, dispatch to join per job, versions and jobs. A job's room opens in place with a way back.
- Capacity test (decision 0022): a gauge button on each agent and in its drawer opens the test dialog (idle check from the captured data, steps up to 8, 16, 32 or 64, stop rules, Cloud and production warnings, consent). Start is disabled until the Phase 2 server runs it. The drawer's Capacity section shows the real spike run on vg-capture (10 of 16 joined, limited by the tester's machine).
- Command palette (cmdk via shadcn Command): ⌘K or the header search opens one list of pages, calls, agents, issues and actions. Hotkeys: G then a letter for each page, ⌘, settings, ⌘⇧L theme, ? for the shortcut list; none fire while typing or with a dialog open. An agent opens from the palette with /agents?agent=<key>.
- Overview redesigned around the fleet (src/features/overview/overview.tsx): four figures with their shape (calls by day, answer time per call against the budget, issues by severity, calls at once); one row per agent with its models, calls, median answer and every turn, where its time goes, peak calls at once, join time and issues; calls whose agent cannot be named (console, local tests, untagged traces) get their own row; the LiveKit project's media server, phone bridge, egress and ingress as four tiles; model providers with the globe, labelled as probed from one agent host. The agent of a call comes from the watched room, else lk.agent_name on its spans. The old layer page (profile-page.tsx, stack-summary.tsx) is no longer routed.
- End to end latency (Overview, src/features/overview/latency-chain.tsx): every stage from the caller's mouth to their ear in four groups (caller to agent, ending the turn, answering, around the turn), each the median over the turns that reported it with its LiveKit source field, per agent or for all agents. Stages LiveKit does not measure (caller network, phone leg per turn, noise cancellation, VAD decision delay, media server to caller) stay listed as not reported. A stacked bar of stage medians sits against the measured end to end.
- Overview as A + B lanes. A, Live wire (src/features/overview/live-wire.tsx, wire-data.ts): AI SDK Elements canvas and nodes on React Flow 12.12.0, callers to phone bridge to media server to each agent with calls to its model providers; link width is calls, label the median ms measured on that hop (not reported where nothing measures it), red when the hop has issues; Magic UI Animated Beam runs only on links of calls still open at the last read; Tremor Tracker strips per layer over the 24 h before the last read. B, Turn anatomy: DevTools lanes from the caller stopping, median bar and p50 to p90 band per stage, not reported lanes for the caller side and the way back, realtime scoped to the agents that run one, field names and turn counts in tooltips. Globe in Model providers. Deepgram re-measured 2026-10-15 at its Virginia host (82 ms median of 12, range 40 to 139 ms). All dependencies pinned exactly.
- App shell: the sidebar follows the Wensity sidebar from 21st.dev (its registry needs a signed-in account, so it is rebuilt on the shadcn sidebar): brand mark and collapse toggle, uppercase section labels, a sliding active pill (motion), count badges, Documentation then Settings, and the account row at the foot. Organisation and project moved to the top bar as two switchers (org / project / page), after the ShipVoice Pro console: a second organisation shows locked as Enterprise (0017); the project menu adds a project, shows what it can reach and opens Settings on Organisation.
- Issues: Open, Resolved and Ignored views; select rows (shared DataTable selection) to resolve, ignore or reopen in bulk, or per row from its menu, each with undo. A resolved issue seen again after it was resolved returns to Open marked Regressed. Kept in this browser (vg.issue-state) until the server stores it.
- Calls: select rows to archive; Archived is its own tab, the stat cards follow the tab, and the call drawer has Archive/Unarchive. Nothing is deleted. Kept in this browser (vg.call-archive).
- Alerts revamp: would-have-fired chart by day and severity, compact stats, rule cards with a severity rail, on switch, count and per-day strip, an Active now pulse and channel toggles; history as a day-grouped timeline.
- Responsive pass: 12 pages at 320, 375, 414, 768, 1024, 1280, 1440 and 1920 px have no sideways overflow and no console errors. Settings and its Access tabs were checked at 320, 360, 390, 768 and 1024 px. Below 1024 px the sidebar is a sheet. On phones, Settings swaps its side menu for a page picker, the call drawer stacks transcript over insights, and Turn anatomy scrolls sideways inside its card.
- Deep links: every page lives under /org/<org>/project/<project>/ (the router's rewrite adds and strips the scope, so internal routes stay short; bare links such as /calls are moved under the current project). The org is default and the project is its name as a slug (/org/default/project/default-project/). Page state is in the query string: tabs, views, time range, table filters and sort, the open call with its turn and pane, open room or agent drawers, the Overview agent, and Settings with its page and Access tab.
- Embeddable widgets (decision 0023): answer time, turn anatomy, calls, one call and issues at /org/<id>/project/<id>/embed/<widget>, with no console shell, ?theme=light|dark, links that stay inside the widgets, and "Powered by VoiceGateway". Settings, Embeds previews the real widget and gives the iframe snippet. Embed tokens and allowed origins are Phase 2 server work; per-customer tokens and removing the branding are Enterprise.
- Live page: deviates from the Phase 1 spec, which asks for an empty state. Agreed 2026-10-14: a continuously running stream in the style of Logfire's live view. Each room is a bar that grows while it is open; clicking it opens the call drawer, or the room drawer when there is no call. It replays the captured rooms at real speed, skipping the hours between watches. The real feed stays Phase 2.
- Call drawer rebuilt from the LiveKit Agents 1.8.6, rtc 1.1.20 and Go protocol 1.52.1 sources: whole-call timeline (turns, caller, agent, tools; zooms to a turn), turn inspector, call summary, event log and trace. Fixtures: two real LiveKit Agent Console sessions (cascaded and gpt-realtime-2) plus the spike calls. No backend yet.

## Next

Plan agreed 2026-10-15: a team builds V1 with Cloud (decision 0024) over 30 days, release target 5 Nov.

1. Freeze the contract: finish Phase 1's OpenAPI spec with x-source, generated types and the mock API, so the lanes build against one interface.
2. Build V1 on its own branch; main keeps the current release until V1 replaces it. Phase 0 (removing the old Python core) lands with that switch.
3. Lanes in parallel: ingest and calls, projects and auth, LiveKit surfaces, actions (alerts, recordings, MCP, capacity test, embed tokens), Cloud control plane with Stripe self-serve, release hardening.
4. One pinned tracking issue on GitHub, split into owned issues with acceptance checks.

## Open Questions

- Cloud billing unit and prices (0024): set in Stripe before launch, never per audio minute.

- Which LiveKit Agents releases we test the observability upload against (0014); the paths carry v0 and can change. Verified against 1.8.7 on 2026-10-16.

- How long an unmatched event waits for its call before it is stored as unmatched (decision 0012 says "briefly").
- Launch and non-functional targets in PRODUCT.md and ARCHITECTURE.md are proposals and need confirming.
- Phase 2 candidate: inferred call labels (triage, disposition) from a self-hosted decision model such as Jev or Kev, opt-in and marked as inferred. Evaluate once V1 traces exist.
- The docs and landing site in site/ still use the old purple theme; when do they move to The Clean Room?

## Known Issues

- The old Python code in src/ no longer matches the docs. It stays until Phase 0.
- Docker Desktop on the dev Mac stopped starting new containers during the spike; it needs a restart.
- Project MCP servers (gopls, shadcn, magicui) need a one-time approval in Claude Code.

### 2026-10-15: landing consolidated into the product repository

The current landing page is imported into `site/landing`, with its prebuilt controls, console visuals, Pages Functions and shared PostHog integration. `site/web` remains the existing D1/API Worker. Cloudflare Pages builds `site/landing` from this repository, and the separate landing repository is superseded. The Site workflow runs `make landing-check` for tests, static export and TypeScript. Installer assets are copied from the repository root before build/dev. See decision 0025.

### 2026-10-15: dashboard contributor baseline repair

PR #329 adds the root console commands from issue #326. Reconcile them with the existing landing targets, include TypeScript in `make lint`, and restore the four local `dashboard/src/lib` helpers previously swallowed by the root `lib/` ignore rule. Keep the contributor's ignore exception and original commits. Add a clean-checkout dashboard CI job so missing source files fail before merge. `make test` runs five threshold-storage regression checks, including malformed values and unavailable storage. Calls-list, call-detail and unknown-value UI tests remain issue #324. This repair does not complete the API contract, mock API, generated types or Phase 1.

### 2026-10-15: contributor layout and Connect execution plan

PR #329 is merged with restored helpers, root commands, five threshold regressions and passing clean-checkout Dashboard CI. This completes the contributor-baseline milestone, not Phase 1 or the eight broad Connect outcomes. Detailed contributor guides and the optional legacy MCP example now live under `docs/contributing/`; root CONTRIBUTING separates current V1 setup from published Python maintenance. The Connect plan in `docs/specs/v1-connect-plan.md` records the root-file audit, ownership, dependencies, PR slices and goal-mode acceptance gates. Active legacy packaging, migrations, Compose and public installer paths are retained until replacement-release proof exists.

### 2026-10-16: source-attributed console read contract (#321)

PR #332 on `feat/dashboard-api-contract` defines the GET operations for every current console data-reading surface (17 at submission, 20 after review): calls, retained recording bytes, LiveKit layer observations, capacity/probes, workspaces and saved configuration. Reader and route inventories cover 23 readers and 15 routes, with static/synthetic assets and local-state boundaries explained. This is the read contract; the console still uses captures until #322/#323 supply generated types and contract-backed mocks.

The maintainer selected explicit source clocks with nullable alignment metadata. Calls retain distinct clocks for spans, turns, events and recordings, with required clock references, observed UTC anchors when available, and observed/inferred/unknown call-start alignment. No missing anchor or alignment becomes a guessed zero. Browser session transport remains unselected by agreement; `x-access` defines required membership, role and resource scope separately from project keys and embed credentials.

Pinned Redocly and JSON Schema validation, source/unit/nullability checks and reader/route coverage run through root commands and Dashboard CI. Local validation passes for 467 attributed schema properties, all 17 tests, lint/TypeScript and the production build. Existing lint/build warnings remain. PR checks provide the CI result for the submitted head; merging requires those checks to pass.

`docs/api/upstream-sources.md` records official LiveKit Docs MCP research, exported declaration inventory across all 151 Go SDK files, direct review of relevant native clients/helpers, and pinned protocol/server/ingress/Agents evidence. It documents native timestamp units and the dispatch/job discrepancy with the docs. Native-first research is now repository guidance and shared MCP configuration.

Review changes (16 Oct): `listCalls` now returns `CallSummary` rows with server-side filters and sort for every column the Calls list filters or sorts on, unknown values sorting last; `getCallStats`, `listIssues` and `listAlertEvents` move the Overview, Issues and Alerts totals to the server over a required range, so the console never totals pages. The clock check now rejects a position bound to a sibling record's clock. Wall-clock instants are RFC 3339 strings throughout (34 epoch fields converted, with `At` names), property names are camelCase, and the checker enforces both. The checker and its tests follow the dashboard code style. 20 operations, 552 attributed properties, 22 tests.

No Go API, authentication implementation, generated TypeScript, MSW, Cloud billing or notification delivery is included. Phase 1 and the broader Connect milestone remain incomplete. Next dependencies are #322 (generated types) and #323 (contract-backed mocks), followed by the scoped backend/console work in `docs/specs/v1-connect-plan.md`.

### 2026-10-16: README product presentation

Prepared a README redesign from the maintainer-selected Langfuse reference: a wide original banner, primary/secondary navigation, a small factual badge row, captured console evidence, a quickstart and an actual turn inspection example. Header, Overview, call inspector and reladraw observation-boundary diagram have separate light/dark assets selected with `picture`. The asset notes preserve the reference mapping, capture provenance and pinned diagram render commands. The Features section lists seven current preview capabilities point by point, with backend-dependent behaviour explicitly planned. Both preview themes load the intended assets without horizontal overflow; local links and the 250 KiB per-file limit pass. Preview and planned V1 capabilities remain explicitly separated. No product code or broader Connect completion changed.

### 2026-10-16: repository social preview

Updated the GitHub social-preview setting with a 1280 × 640 card matching the README wordmark and blue accent. Reloaded Settings to verify the upload persisted. The old cost/quality and multi-framework claims are removed. Both light/dark exports and their shared editable banner source are retained with the README assets; GitHub uses the light export because the setting accepts one fixed image.

### 2026-10-16: runnable foundation (phase02, in progress)

`docs/specs/phase02-foundation.md` starts the Go platform. `cmd/voicegateway` runs `web`, `worker` and `preflight`; `internal/config` reads `VOICEGATEWAY_*` and names every missing variable without echoing a secret; `internal/preflight` checks Postgres, ClickHouse, Redis and the S3 bucket concurrently with a timeout each and reports every failure; `internal/obs` gives JSON logs and `/healthz`; `internal/app` wires the roles. `deploy/compose.yaml` runs the six services from images pinned by tag and digest, and `make up` creates `deploy/.env` with random secrets. MinIO no longer publishes images, so the bundled bucket is SeaweedFS (decision 0026). Lint, format and `go mod tidy` run through tool versions pinned in `go.mod`, in pre-commit and in the new Go workflow, which also starts the stack and runs preflight against it.

Acceptance checks, measured on PR #336: (1) CI `compose` job: `make up` brought all six services up with dependency health checks passing and `/healthz` answered 200, then `make preflight` reported OK for all four. (2) `TestPreflightAllReachable` and (3) `TestPreflightReportsEveryFailure` pass against testcontainers in CI (they need Docker, which does not answer on the dev Mac). (4) `TestConfigMissingAndRedacted` and (5) `TestComposeImagesPinned` pass locally and in CI. (6) golangci-lint 0 issues and `go test -race` pass. SeaweedFS confirmed as the bundled bucket (decision 0026). No tables, migrations or ingest yet.

### 2026-10-16: generated API types (#322)

The console now imports its API data types from `dashboard/src/lib/api-types/`, generated from `docs/api/openapi.yaml` by `@hey-api/openapi-ts` 0.99.0 (`openapi-typescript` 7 supports only TypeScript 5). `npm run api:types` or `make api-types` regenerates them, and `make api-check` fails when the committed types are out of date. The only console-side extension is the dev design preview marker on recordings. Capture fixtures predate the contract, so `fromCapture` in `src/lib/api.ts` adds the required id, end state, tags, archive flag and unknown-alignment clocks without inventing values; #323 replaces it with contract-backed mocks. Values the contract marks nullable now show as Unknown or are left out of timelines and findings instead of rendering 0 or NaN.

### 2026-10-16: first real call, slice A (projects and storage)

`docs/specs/phase03-first-call.md` splits the phase in two. Slice A, on `feat/first-call`: `voicegateway migrate` applies embedded goose migrations (`migrations/postgres`), and Compose runs it once before web and worker. Postgres holds organisations (the OSS `default` is created by the migration) and projects; a project's LiveKit API secret is sealed with AES-256-GCM under the new required `VOICEGATEWAY_SECRET_KEY`, bound to its organisation and project, and no read returns it. `voicegateway project add` (and `make project-add`) takes the secret only from `LIVEKIT_API_SECRET` and refuses a LiveKit API key another project already uses, naming that project, because ingest finds the project by that key. Queries are generated by sqlc (a `tool` directive, checked by `make go-lint`). `make up` adds `VOICEGATEWAY_SECRET_KEY` to an existing `deploy/.env`.

Slice A acceptance checks 1 to 3 (`TestMigrateTwice`, `TestProjectSecretSealed`, `TestProjectDuplicateLiveKitKey`) run against Postgres in CI; 4 (`TestOpenWithWrongKey`) passes locally; 5 is the CI compose job, which now also adds a project and checks a duplicate key is refused. Slice B (authenticated ingest to ClickHouse) is next.

### 2026-10-16: first real call, slice B (a) (webhooks to the stream)

`web` serves `POST /livekit/webhook`. livekit/protocol v1.52.1 `webhook.Receive` verifies the JWT against the LiveKit key it names and the body's SHA-256; the key provider looks the project up in Postgres and opens its sealed secret, so each event is attributed to the project that signed it. A verified event becomes an `internal/event` envelope (LiveKit's event id, kind, project, received time, body unchanged) appended to the `voicegateway:ingest` Redis stream through `internal/stream`, which also holds the worker's consumer-group reader. Bad signatures are 401, bodies over 1 MiB 413, a failed project lookup or stream append 503 so LiveKit retries. No body or secret is logged.

Acceptance checks 6 to 9 pass locally against Postgres and Redis containers and run in CI. Manual check: a real `livekit-server --dev` 1.13.3 sent room_started and room_finished for a test room, both appended for project `default/dev`; an unsigned post answered 401 and appended nothing. Next: (b) the Agents observability uploads, (c) the worker into ClickHouse.

### 2026-10-16: first real call, slice B (b) (agent observability uploads)

`web` serves LiveKit's four observability paths (decision 0014) as LiveKit Agents 1.8.7 sends them: OTLP traces, logs and metrics (gzip, protobuf) and the multipart session report. The bearer JWT is verified with livekit/protocol against the project that owns its key and must hold the observability write grant. OTLP bodies are stored decompressed; the session report keeps the recording header (as protojson) and chat_history.json, and is identified by the job id. The audio part is read and discarded, and the report marks it, until a project has a recordings bucket (0015).

A failed lookup or append answers 503 with google.rpc RetryInfo, because the 1.8.7 session uploader retries a 5xx only with it and otherwise drops the report. Acceptance checks 10 to 13 pass locally against Postgres and Redis containers and run in CI. Manual check: a real LiveKit Agents 1.8.7 agent on a LiveKit Cloud test project, with `LIVEKIT_OBSERVABILITY_URL` pointed at a local stack, uploaded traces, logs, metrics and its session report (room, job and start time in the header; audio discarded), all attributed to that project. Next: (c) the worker into ClickHouse.

### 2026-10-16: first real call, slice B (c) (worker into ClickHouse)

`worker` now stores the ingest stream in ClickHouse. `migrations/clickhouse` (goose) creates `events`, one row per envelope, ReplacingMergeTree ordered by project, kind and dedupe key; `voicegateway migrate` applies it after Postgres. The sink (`internal/sink`) claims entries a dead worker left pending, skips dedupe keys already stored (the event id, else the stream entry id), inserts in batches, and only then acknowledges with XACKDEL so the stream does not grow. Undecodable entries go to `voicegateway:ingest:dead` with their fields. When ClickHouse refuses a batch for its data (its data error codes, or a value that does not convert), the batch is halved until the refused rows are found and only those are dead-lettered; any other failure is retried with backoff and a 30 s timeout per call, acknowledging nothing; inserts carry at most 32 MiB of payload, and a batch that fails three times in a row for a non-data reason is halved, so a size-related failure cannot wedge the worker. `room.turnPassword` is removed from webhook bodies before storage, and blanked in anything dead-lettered.

Acceptance checks 14 to 22 pass locally against Redis and ClickHouse containers and run in CI. Manual check: the 14 envelopes from the slice (a) and (b) runs, kept in Redis across `make down`, were stored on the first start; with the worker stopped, a room created and deleted on `livekit-server --dev` 1.13.3 waited on the stream and was stored when it started; after `make down` and `make up` all 16 rows remained, with no duplicates. With this, every Phase 3 acceptance check has a measurement; calls are not joined yet, which the spec leaves to the next phase. Spans are stored as whole OTLP batches; deduping spans on trace and span id belongs to the span decoder in the next phase.

### 2026-10-16: fresh-install rehearsal

A clean clone of main (8fcd22d8) in a scratch directory, under its own Compose project name so no existing volume was reused:
- `make up` created `deploy/.env` (mode 600, untracked) and brought all six services up healthy. The image also builds with `--no-cache` in 30 s.
- `make preflight` reported OK for Postgres, ClickHouse, Redis and the bucket.
- `make project-add` added a `livekit-server --dev` project and a LiveKit Cloud test project, and refused a missing secret, a missing name and a key already in use.
- A room created and deleted on `livekit-server --dev` and a LiveKit Agents 1.8.7 session on the Cloud project gave 8 rows in ClickHouse.
- After `make down` and `make up` the 8 rows remained, migrations applied nothing, the logs held no secret and no ERROR line.

Two rough edges were fixed: the "missing" message now lists fields in a fixed order (it followed Go map order), and `make project-add` without `NAME` says how to set it instead of naming the `--name` flag. "Repeatable installs" on the plan stays unticked until a teammate repeats this on their own machine.

### 2026-10-16: mock API, part 1 of 3 (#323)

The console starts reading through the API. MSW 2.15.0 serves the contract's paths from the captured fixtures (`src/mocks/handlers.ts`), and TanStack Query 5.104.0 holds the reads. The handlers act as the server: `listCalls` pages `CallSummary` rows computed with the call drawer's own checks (`src/mocks/summaries.ts`); `getCall` returns one call, and 404 in the contract's error shape for an unknown id or project. The Calls page, the call drawer and the calls and call widgets now fetch. Each has a loading state, an error state that says when no API answered, and a not-found state. `VITE_MOCK_API=off` turns the mock off. `tests/mock-api.test.ts` checks every response against `docs/api/openapi.yaml` itself. The Calls filters run on the server: each chip offers only the operators the contract can express and becomes a `listCalls` parameter (range, channel, issues, answer time, length, turns, unknown, recorded, tag, archived); anything else ("or", "not") is named on the page and not applied. The contract gains `CallPage.totals` (figures over every matching call, not a page) and `CallSummary.pipeline`, so the stat cards follow the filters and the row label keeps its pipeline. Archive and tags stay in the browser until the server stores them; the mock reads the same storage, so they filter on the server side too. Calls with nothing to check report unknown issues, never zero, and a channel without evidence is unknown. The other pages still read the fixtures directly; parts 2 and 3 move them.
