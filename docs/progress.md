# Progress

<!--
Updated at the end of every phase, by you or by /close-phase.
A fresh session reads this to know where the project stands.
-->

## Current Phase

Live operations (2026-10-20): sampled refresh and permission-loss handling merged in #429,
project-scoped pushed notifications in #430, and real container resource collection in #431
(81d978ef97ce6051f5753b92e4be1a67243f30ac). Native source, stale/disconnect/recovery,
HTTP permission and browser checks passed before merge. This roadmap task is complete.

Issue workflows (2026-10-20): call tags/archive merged in #435
(82c15b084831fb2b66ca253a7789c5416c20939c). The current branch adds persisted call findings,
scoped issue decisions/tags/archive, revision-safe Undo, snapshot-consistent reads and historical
backfill without summary rewrites. Targeted native race checks and Go lint pass; 232 frontend and
88 browser checks passed before the backfill addition, and a clean full final run is still required. The backfill test now explicitly covers a backlog
larger than one sweep after the full run exposed its original first-batch assumption. Standing
component evaluation and webhook delivery are unfinished. Complete remains 2/14; do not count
these partial issue/settings changes as completed tasks.

Call diagnosis (2026-10-19): turn-level latency comparison, trace-clock response breakdown, source-linked speech/tools and reported usage are implemented. Turn navigation stays in the fixed header and bounds comparison to six turns. Realtime calls show speech intervals, derived response gaps and interruptions without inventing STT/TTS stages. SIP termination remains separate evidence. The generic per-call architecture flow and topology were removed.

Capacity UI (2026-10-19): an explicit setup/start/results preview includes generated load, SFU and worker impact, component metrics and a zoomable topology with unknown regions kept separate. Data is synthetic and the preview places no calls. Real runners, attempt/concurrency instrumentation, cancellation and cleanup remain unfinished. See docs/specs/capacity-telemetry-audit.md.

Phases 01 to 06f are on main (06f, adding a project from the console, merged in #419). The next phase has no spec yet.

## Done

| Phase | Spec | Result | Date |
| --- | --- | --- | --- |
| Setup | none | PRODUCT.md, ARCHITECTURE.md, DESIGN.md (The Clean Room seed), CLAUDE.md and decisions 0005 to 0013 written for the Go platform | 2026-10-12 |
| Call reads by index | none | getCall and the summary rebuild scanned every span, metric point and event of the project, since call_id is in no ClickHouse sorting key. ClickHouse 00008 adds a bloom filter index on call_id to spans, trace_calls, metric_points and events (materialized for old parts), and CallSpans names the call's traces first so the read uses the sorting key. TestCallSpansSkipsOtherCalls: 1 of 4 granules read, 4 of 4 before. Raw telemetry retention is done: ClickHouse 00010 expires events, spans, metric_points, trace_calls and unmatched after 90 days (TestRawTelemetryExpiresAfter90Days) | 2026-10-18 |
| Data model tune-ups | none | Checked against ClickHouse/agent-skills clickhouse-best-practices (356a8c1) and supabase/agent-skills supabase-postgres-best-practices (c9be0e9). CountUnlinkedTraces and chatHistory read spans and events without FINAL (same answers; TestCountUnlinkedTracesIgnoresDuplicateSpans), and the unmatched check reads only the lookback window. ClickHouse 00009 sets ttl_only_drop_parts on the nine 30-day read tables (whole-day parts drop, so a row may live up to a day past 30) and makes spans.name LowCardinality. Layer poller inserts are async with wait_for_async_insert (TestStoreReadIsReadableOnReturn). Postgres 00008 adds calls_start_at (StatsCalls on 200k calls: 72.7 ms bitmap scan plus sort before, 6.8 ms index scan with no sort after), memberships_organisation and project_keys_created_by, built CONCURRENTLY. Raw telemetry retention is done: 90 days, ClickHouse 00010 (#401). Not done: a participant-count column on calls | 2026-10-18 |
| API reference | none | The console serves the API reference at /docs (sidebar and command palette): Scalar (@scalar/api-reference-react 0.9.79, MIT) renders docs/api/openapi.yaml with schemas and a request runner on the signed-in session, in its own lazy chunk, with Scalar's fonts, telemetry, proxy, AI chat and MCP offer off; no request leaves the origin (checked in Chromium). surfaces.json lists /docs with every operation, since its runner can send any | 2026-10-18 |
| Console in the image | #394 and this PR | The web image builds dashboard/ in a pinned node:24.22.0 stage and web serves it from VOICEGATEWAY_CONSOLE_DIR (decision 0011), so `make up` gives a working console at :8080 with no Node step. Deep links get index.html (no-cache), hashed assets are cached as immutable, a missing asset or unknown /api/ path is a 404; files are gzipped by klauspost/compress gzhttp (router chunk 4.2 MB to 1.26 MB); GET on the webhook and upload paths is a 405 with Allow: POST, never the console. Pair review passed with an 85 MB image and a real LiveKit call at 7 sizes | 2026-10-18 |
| MCP server | none (plan task dev-mcp) | web serves MCP at /mcp over Streamable HTTP, stateless, with the official MCP Go SDK (github.com/modelcontextprotocol/go-sdk v1.8.0). Its 16 read-only tools are generated from docs/api/openapi.yaml (embedded through docs/api/contract.go): every GET a project key may call that the router serves and that answers JSON, so live-events (SSE) and recording audio are left out. A tool runs its operation through the /api/v1 router in process, behind the same role table; /mcp runs the same gate (auth.Service.Gate) and admits a project key only, so a console session cookie never reaches it. Organisation and project come from the key, never from the agent. TestMCPToolsReadTheKeysProject runs each tool's happy path on real Postgres and ClickHouse, checked against the contract, plus a refusal or failure per tool and a revoked key stopping every tool | 2026-10-21 |
| Embed tokens and compact widgets | docs/specs/embed-tokens.md (plan task dev-embeds) | Decision 0034. createEmbedToken (a project key or a member) returns vge_ plus an HS256 JWT from golang-jwt v5, key derived from VOICEGATEWAY_SECRET_KEY with HKDF, naming one project, its widgets, one allowed origin, the minter and an expiry of 1 minute to 1 day. The gate checks it on every request and, in one query, that the minting key or member is still active and the origin still allowed; Admins set allowed origins (updateAllowedOrigins, PG 00021). Widget pages send frame-ancestors with the token's origin only for a valid token for that project and widget, every other console page frame-ancestors 'self', and widget pages no Referer. An embed token reads its widgets' routes only (embedAccess, compared with x-embed-access). The widgets are five compact tiles in iPhone sizes (answer time, calls, issues, recent calls, time per turn) plus the one-call view, chosen by the maintainer on 2026-10-21; Settings, Embeds previews each size and makes a test token. TestEmbedTokensFrameOneProjectForOneOrigin covers the doneWhen on real Postgres | 2026-10-21 |
| Go spike | none | Four gates passed: ingest (0 lost in 12 runs), LiveKit webhooks in Go, Agents spans joined to calls, ocb collector build. Results in decision 0009 | 2026-10-11 |
| 05c egress and ingress | docs/specs/phase05-livekit-layers.md | The worker lists egress and ingress per project every 15 s and stores redacted projections (ClickHouse 00006); getEgress and getIngress serve them with sourceStatus and histories joined with stored webhooks; Egress gains streams and outcome (#345, LIMIT_REACHED reads as cut_short). Checks 8 (TestEgressLive) and 9 (TestIngressLive) pass against livekit-server --dev 1.13.3, with LiveKit's own IOInfo client playing the egress and ingress services | 2026-10-18 |
| 05d agent workers and Cloud agents | docs/specs/phase05-livekit-layers.md | `voicegateway project workers` add, remove and list keep each project's worker metrics URLs in Postgres `project_worker_endpoints` (00006), sealed under VOICEGATEWAY_SECRET_KEY with only scheme and host in the clear. The worker reads them every 15 s (lk_agents_worker_load, lk_agents_active_job_count per nodename, prometheus/common expfmt) and LiveKit Cloud agents every 60 s (AgentClient.ListAgents, ListAgentVersions), storing worker_samples, worker_endpoint_reads and cloud_agent_reads (ClickHouse 00007). getWorkers and getCloudAgents serve them with sourceStatus; Workers gains endpoints and per-worker endpointId and sourceStatus; a self-hosted server answers Cloud agents as not applying with no Cloud call. Checks 10 (TestWorkersFromMetrics, synctest), 11 (TestCloudAgentsSelfHosted) and 12 (TestReadsMatchContract, all six reads) pass. Not additive: CloudAgents versions[].createdAt and deployedAt were widened to nullable, against the spec's "changes stay additive", following its "a zero or absent source time is null" and CLAUDE.md's unknown-never-zero; the dashboard already handles null. A worker's id is now endpointId/nodename (Prometheus has no LiveKit worker id). Before any API or dashboard form can add endpoints, the worker needs an SSRF guard that rejects link-local and metadata addresses at dial time; today only the CLI adds them | 2026-10-18 |
| Webhook secrets | #386 | The sink stores egress_* and ingress_* webhooks without stream keys, URL credentials, storage credentials, signing keys, participant names or metadata, and masks phone numbers; unknown status names newer than protocol v1.52.1 are kept. Webhooks stored before this fix keep their secrets until the 90-day events TTL (00010) removes them, so reset dev and loop stacks | 2026-10-18 |

## In progress (Phase 1)

- Invitation delivery: persist send-start and confirmed failure, detach bounded sending and outcome recording from request cancellation, and preserve unknown SMTP acceptance as null for instance invitations. Concurrent idempotent repeats wait for the first outcome. Uses go-mail v0.8.1 delivery state rather than treating cleanup errors as rejection. Local Go race/lint pass; container-backed acceptance checks require CI. Deploy the Cloud nullable-outcome consumer before this engine change.

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
2. Build V1 on its own branch; main keeps the current release until V1 replaces it. Phase 0 (removing the old Python core) is done (2026-10-18).
3. Lanes in parallel: ingest and calls, projects and auth, LiveKit surfaces, actions (alerts, recordings, MCP, capacity test, embed tokens), Cloud control plane with Stripe self-serve, release hardening.
4. One pinned tracking issue on GitHub, split into owned issues with acceptance checks.

## Open Questions

- Cloud billing unit and prices (0024): set in Stripe before launch, never per audio minute.

- Which LiveKit Agents releases we test the observability upload against (0014); the paths carry v0 and can change. Verified against 1.8.7 on 2026-10-16.

- How long an unmatched event waits for its call before it is stored as unmatched (decision 0012 says "briefly"). Phase 04a: a webhook or session report is counted at once; a trace after 15 minutes. Confirm or change those numbers.
- Launch and non-functional targets in PRODUCT.md and ARCHITECTURE.md are proposals and need confirming.
- Phase 2 candidate: inferred call labels (triage, disposition) from a self-hosted decision model such as Jev or Kev, opt-in and marked as inferred. Evaluate once V1 traces exist.
- The docs and landing site in site/ still use the old purple theme; when do they move to The Clean Room?

- The worker's LiveKit reads (media server, SIP, egress, ingress, dispatch) dial each project's LiveKit URL with no address guard: server-sdk-go v2.19.0 builds its own `http.Client` over `http.DefaultTransport` for those clients, so `code.dny.dev/ssrf` and `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY` cover worker metrics endpoints only. Now that an Admin can add a project from the console (06f), a shared Cloud instance needs a guard on these dials before the form is open to customers.

## Known Issues

- Docker Desktop on the dev Mac stopped starting new containers during the spike; it needs a restart.
- Project MCP servers (shadcn, magicui) need a one-time approval in Claude Code. The gopls MCP server was removed on 2026-10-17: its fsnotify watcher opened one file handle per file in the tree (about 61,000) and exhausted the system file table.

### 2026-10-18: marketing site moved to a private repository

`site/landing` and `site/web` moved to the private `voicegateway-web` repository, which the Pages project `voicegateway-web` builds for voicegateway.dev. This repository keeps `site/docs`, `site/theme.css`, `install.sh` and `collector.sh`; the private site downloads the two scripts from main on every build. The Site workflow now checks only the docs. See decision 0030, which supersedes 0025.

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

The console starts reading through the API. MSW 2.15.0 serves the contract's paths from the captured fixtures (`src/mocks/handlers.ts`), and TanStack Query 5.104.0 holds the reads. The handlers act as the server: `listCalls` pages `CallSummary` rows computed with the call drawer's own checks (`src/mocks/summaries.ts`); `getCall` returns one call, and 404 in the contract's error shape for an unknown id or project. The Calls page, the call drawer and the calls and call widgets now fetch. Each has a loading state, an error state that says when no API answered, and a not-found state. `VITE_MOCK_API=off` turns the mock off. `tests/mock-api.test.ts` checks every response against `docs/api/openapi.yaml` itself. The call drawer is `/calls/<call id>`, read with `getCall` whatever the filters, so a reused room never opens the wrong call; every link to a call (Alerts, Issues, Live, the room drawer, the command palette) passes its id. The list is read a page at a time (`useInfiniteQuery`, Show more) in the server's sort order. The Calls filters run on the server: each chip offers only the operators the contract can express and becomes a `listCalls` parameter (range, channel, issues, answer time, length, turns, unknown, recorded, tag, archived); anything else ("or", "not") is named on the page and not applied. The contract gains `CallPage.totals` (figures over every matching call, not a page) and `CallSummary.pipeline`, so the stat cards follow the filters and the row label keeps its pipeline. Archive and tags stay in the browser until the server stores them; the mock reads the same storage, so they filter on the server side too. Calls with nothing to check report unknown issues, never zero, and a channel without evidence is unknown. The other pages still read the fixtures directly; parts 2 and 3 move them.

### 2026-10-16: mock API, part 2 (#323): Egress, Ingress, SIP

The Egress, Ingress and SIP pages read `getEgress`, `getIngress` and `getSip` through the mock API. `src/mocks/livekit.ts` adapts the captured reads to the contract: Unix seconds become RFC 3339 (an unreported time stays null), and fields the contract does not carry are left out. The pages now use the generated types instead of their own, so values the contract allows to be unknown read as unknown: an egress with no reported length or size is not counted as lost or as zero seconds (the audio total says "at least" when a length is missing), and a SIP check fires only when a setting is reported off (an unknown auth, PIN, phone-number hiding or agent count no longer reads as off). The SIP page's hand-written layout example (`?data=test`) stays a local file, marked as such. The shared loading and error states moved to `src/components/page-state.tsx`. Next: part 3 (media server, agents, workers, capacity, project snapshot, probes), then part 4 (issues, alerts, overview with call stats, live, palette, settings). Before the egress alert (#341): the `Egress` contract carries no `streams`, so per-output failures cannot be shown, and `EGRESS_LIMIT_REACHED` has no "recording cut short" check yet; add both to the contract and the page. The adapter's null-time path has no captured example; a fixture row with an unreported time would test it.

### 2026-10-17: mock API, part 3a (#323): media server

The Media server page, the room drawer and the SIP page's watched calls read `getMediaServer`. The mock adapts the capture's millisecond and second times to RFC 3339 (a recording's id is its playback URL). On the client, `fromContract` in `room-drawer.tsx` is the one place the contract becomes the drawing code's view, with times back as epoch numbers in the units it uses; every time is nullable there, so each place now handles an unreported time: a room or a stay without times is left off the clock and the charts, a timeline anchors on the first event when creation was not reported and counts steps without a time, and lengths, join times and ring times read "unknown". Pages not yet moved (Live, Agents, Overview, Alerts, Issues) read the same view from the mock directly and gained the same null guards. Next: part 3b (agents, workers, capacity, project snapshot, probes).

### 2026-10-17: mock API, part 3b (#323): agents, capacity, project

The Agent workers page (with its drawer and the capacity dialog) reads `getCloudAgents`, `getMediaServer` and `getCapacity`; the Project page reads `getProjectSnapshot`. The mock drops the capture's deployment enabled flag and event log, renames the capacity steps' snake_case fields, turns the snapshot's seconds into RFC 3339 and gives the observed agent's options camelCase names. `agentsFrom` builds the agent list from the two reads; a legacy `agents()` serves the pages not yet moved. Capacity values the contract allows to be unknown stay out of totals and medians (the joined total reads unknown if a step did not report it), a capacity run that could not be read says so instead of "not measured yet", and a snapshot with no observed agent says so. Next, part 4: issues, alerts, overview with workers, probes and call stats, live, command palette, settings; plus room links by room sid, `Job.dispatchedMs` as `number | null` (Alerts reads it unguarded), and making `RoomDrawer`'s `ms` required.

### 2026-10-17: mock API, part 4a (#323): issues, alerts, palette, nav badge

The Issues and Alerts pages read `listIssues` and `listAlertEvents`; the mock computes both with the console's own checks (`src/mocks/insights.ts`) over a required from/to range, and the pages ask for all of time up to now until they gain a range picker. Each page's view adapter (`issuesFrom`, `rulesFrom`) turns the contract into its rows; the seven alert rules' titles and conditions are page configuration keyed by rule id. An alert event with no reported time is listed under "Time unknown" instead of 1 January 1970, and a failed job with no dispatch time is no longer placed at NaN. The command palette reads the recent calls, the agents and the issues when it opens, and the Calls badge is the server's count. The embed's issues widget reads `listIssues` too. Issue dispositions and alert rule setup stay in the browser until part 4b. Next, part 4b: Overview with `getCallStats` (and a test that `CallPage.totals` and `getCallStats` agree on the median answer time and turn count for the same range), `getWorkers` and `getProviderProbes`; Live; settings and the remaining reads; room links by room sid; `RoomDrawer`'s `ms` required once nothing reads the legacy data.

### 2026-10-17: mock API, part 4b-1 (#323): Overview on getCallStats

The maintainer chose to extend the contract so Overview keeps its per-turn and per-call detail. `CallSummary.agentKey` names the agent that took a call (its watched room's agent, else `lk.agent_name` on its spans). `CallStats.answers.values` replaces `valuesMs`: each measured turn carries its call id, room, turn and agent. Each agent row adds its calls with their median answer (at most 500, `callsTruncated`) and `anatomyTurns`, which is also at the top level. `issueCounts` adds `info`, so Overview's issue total matches the Issues list. `StageTiming.p90Ms` is described as what the chart draws: the median start plus the 90th percentile length. Overview's figures, the Every turn strip, Turn anatomy and the agent cards read `getCallStats`; the fleet, rooms, SIP, Egress, Ingress, worker load and provider probes read their own endpoints (`getWorkers` and `getProviderProbes` are new mock handlers). The answer-time and turn-anatomy embeds read `getCallStats` too. Overview covers the seven days up to the latest call (the Calls page's default range) and says so. Tests check that `getCallStats` agrees with `CallPage.totals` for the same range on calls, measured turns, median answer, turns over budget and channels, and with the Issues list on issue counts; that each agent row counts exactly the calls the list names it on; and that an unmeasured call keeps a null median. Compared on the captures, every figure on the page is unchanged. The Live wire and the stage chain under Turn anatomy still read the captured calls, grouped by the same agent key. Next, part 4b-2: their reads, then Live, settings, room links by room sid and removing the legacy fixture paths.

### 2026-10-17: mock API, part 4b-2 (#323): Live wire and stage chain

Overview no longer reads any fixture. `CallStats` adds `chain` (every stage of a turn as its own median duration, with the longest for stalls, at the top level and per agent), per-agent `providers` (the providers each agent's turns used, from the model on each stage's span, with each stage's median there) and `answerTurns`, and `byHour` (calls and median answer per UTC hour). The stage chain under Turn anatomy reads `chain`; the agent's join comes from the fleet's jobs and the hop back from the provider probe, as before. The Live wire takes the stats, the media server watch, SIP and the probes as arguments instead of importing captures. An unknown count draws the thinnest link and reads unknown in its tip, never zero. The wire's strips now use UTC-hour blocks, the last one holding the last read, so a block lines up with `byHour`; before, they ran back from the exact last read. Compared on the captures, Overview's text is identical for all agents and for a single agent. Tests add: the hourly and daily counts add up to the call count, turn values come newest call first, a call starting exactly at `to` is left out, chain stages stay within the turns counted, provider calls within the agent's calls, and realtime turns belong only to the row with no agent. Next, part 4b-3: Live; then settings, room links by room sid and removing the legacy fixture paths. Still open, from the pair review of #349: how undated issue hits are shown in a range (`IssueList` has no marker for a standing state versus a dated event, and the "jobs failed" and "joins" aggregates carry no time).

### 2026-10-17: mock API, part 4b-3 (#323): Live, room links by room SID

The Live page reads `getMediaServer` and replays its rooms as before. A room opens its call by the room's SID (`callInRoomQuery`: `listCalls` filtered by room name, then the summary whose `roomSid` matches), so a reused room name never opens another room's call; a room with no matching call, or a lookup that fails, opens the room drawer. The room drawer's "Open call" link uses the same lookup and says "Not in Calls" when nothing matches, instead of assuming a call exists. `RoomDrawer` now requires the media server read, and the call view's path strip reads it instead of the fixture (it says it is loading, or that the read failed, rather than showing a room as not watched). On the captures, the rooms that open calls are the same two as before. Next, part 4b-4: settings reads, then removing the remaining legacy fixture paths (`data` in room-drawer, `agents()`, `issues()`, `rules()`, `listCalls`/`getCall`).

### 2026-10-17: mock API, part 4b-4 (#323): settings reads

Settings now read through the contract: `getProjectSettings` (thresholds, bucket, retention, embed origins), `getIssueDispositions`, `getAlertSetup`, `getInvitations`, `getWorkspaces` and `getRecordingMedia`. The console's saves (thresholds, issue decisions, alert setup, invitations) still stay in this browser until web stores them; they go through `lib/local-store.ts`, and the mock API reads the same storage and validates it as the server will, so a malformed or out-of-range value is left out or falls back on its own instead of reaching a page. A save asks the read again; a threshold save asks every read again, because the server's judgements (`answers.budgetMs`, `overBudget`) use the stored threshold. The app shell waits for the project's settings before it renders a page, and if they cannot be read the page says the default thresholds are in use. Workspaces are read once at start, before the router resolves which project a URL names; the placeholder `workspace-switcher.tsx` is gone. A link naming an organisation or project the read does not list keeps its URL and shows "No such project" with a link to the first readable project, never another project (the Workspaces contract); a bare link such as `/calls` still moves under the current project. Bucket, retention and allowed origins are null (not configured), never zero or a guessed default. Recordings play from `/api/v1/org/<org>/project/<project>/recordings/<id>` with the room SID as the id, served with byte ranges for the player; an id no capture names, a path, or a capture whose file is not on this machine is 404, never another file or the app's page. The embedded answer-time widget takes its budget from `getCallStats` instead of the console's threshold, since an embed token cannot read settings. Tests add: the contract shape of every new read, null for unconfigured settings, thresholds falling back per field, a saved threshold changing the server's budget, malformed decisions, rules, channels and invitations left out, another project or organisation refused, and recording audio with ranges. Next: removing the remaining legacy fixture paths, then how undated issue hits show in a range.

### 2026-10-17: phase 04a, calls, participants and span links

The worker joins each event to its call before storing it (`internal/join`, spec `docs/specs/phase04-call-join.md`). Postgres gains `calls`, one per project and room SID, and `call_participants`; ClickHouse gains `spans` (decoded from the OTLP trace exports with pdata, folded on trace and span id), `trace_calls`, `unmatched` and `events.call_id`. Webhooks join by the room SID (an Egress's `room_id`, a job's room), Ingress by room name at its reported time; session reports by `room_id`, else job id; spans by their own `room_id`, `job_id`, `lk.room_sid`, `lk.job_id` in that order, never the resource, and a span with none resolves through its trace. Following the pair review, a span that starts more than a minute after the call its ids name has ended is stored unjoined, since LiveKit Agents falls back to the last job's ids between jobs; the minute covers shutdown spans and clock skew. A row Postgres refuses for its values (a NUL byte, invalid UTF-8, a missing project) is dead-lettered on its own instead of holding up the stream. Span times the exporter did not send are stored null. Every call time is nullable and set only from a reported value. Postgres writes come first, then spans, links and unmatched rows, then the event row, then the ack, so an event row with a call id always has its call. Every minute the worker closes calls with no reported activity for 2 hours as inferred (end = last reported activity, null if none) and counts traces unlinked 15 minutes after their last span, once each.

Acceptance checks 1 to 11 pass locally against Postgres, ClickHouse and Redis containers and run in CI, plus tests for the stale-id guard, refused rows, id order within one export and spans without times; mutating the guard, reading resource ids or dropping the refused-row mapping fails them. Two workers sweeping at once can write an unlinked trace twice until ClickHouse merges it, so readers of `unmatched` count with FINAL. Manual check passed on 2026-10-17 (#362 merged as ce0031e3): a real LiveKit Agents 1.8.7 agent on the LiveKit Cloud test project (LiveKit Inference, a scripted caller), with the webhook and `LIVEKIT_OBSERVABILITY_URL` pointed at the stack through a cloudflared tunnel. Result: 10 webhooks verified and stored, all on the call; one `calls` row with the room SID, job id, start from `creation_time_ms`, end from `room_finished`, `observed`; the caller (`standard`) and the agent in `call_participants` with join and leave times; all 56 spans of the job joined by their own ids, 1 trace link, every trace export row carrying the call id; no `unmatched` row. Three shutdown spans (`job_shutdown`, `session_end_upload`, `room_disconnect`) start 0.72 to 0.95 s after `room_finished`, inside the 1-minute grace, partly because LiveKit stamps `room_finished` in whole seconds; none fell outside it. After `down` and `up` the counts were identical (37 events, raw equal to folded, 113 spans: 57 from the earlier agent-only call and 56 from this one), with no duplicates. An earlier agent-only call showed the gap fixed before merge: trace export rows had no call id. Next: 04b, turns, latencies and usage from the session report and the spans.

### 2026-10-17: phase 04b, turns, latencies and usage

`internal/turn` builds one turn per `agent_turn` span at read time, and `chstore.CallTurns` serves it from the stored spans, session report and usage points (spec `docs/specs/phase04b-turns.md`). Each turn carries:
- the first token of the reply the caller heard, which with preemptive generation is not the first `llm_node`;
- llm1 and llm2 around tools, tool time, transcription, end of turn, hook, TTS first byte and end to end;
- tokens and TTS usage, with the count of requests that did not report them;
- user and agent text paired from `chat_history.json` by message order.

LiveKit's unavailable sentinels stay unknown. STT audio seconds come from the agent's `lk.agents.usage.stt_audio_duration` delta counter. The worker now stores each `lk.agents.usage.*` point in `metric_points`, joined by its own room and job ids, because one export can carry two jobs' points. The call's STT audio is the sum of its points, null when none joined.

Tests cover checks 1 to 10 against the console capture (5 turns, field for field), the real 2026-10-17 call (preemptive generation) and its metric exports (33.85 s and 33.25 s). Mutations fail them: dropping the trace-link read, or reading points without FINAL after a redelivered export.

### 2026-10-17: mock API, part 4b-5 (#323): legacy fixture paths removed

No page imports a fixture or computes a server read any more; every page reads through `src/lib/api.ts`. The room drawer's `data` export and the default arguments of `openAtWatchEnd` and `windowOf` are gone, so callers pass the media server read they made. The Issues and Alerts computation (`issues()`, `rules()`) and the legacy `agents()` moved into `src/mocks/insights.ts`, which runs the same checks over the mock's contract reads (`capturedCalls`, `cloudAgents`, `mediaServer`, `sip`, `egress`, `ingress`); `issues-model.ts`, `alerts-model.ts` and `agents-model.ts` keep only the pages' view adapters and checks. `listCalls` and `getCall` over the fixtures are gone from `api.ts`. The project switcher and Settings' organisation pane read the server URL from `getMediaServer`; the Data pane's recording count is `CallPage.totals.recorded` of `callCount` (an unknown total reads unknown); the Embeds preview picks the latest call with a turn through `listCalls`. The provider globe and the turn waterfall read `getProviderProbes` instead of `network-probe.json`; an embedded call, whose token cannot read the probes, draws no network part, and a probe with no location is left off the globe. On the captures, `listIssues`, `listAlertEvents` and `getCallStats` answer byte for byte what they did before. A test checks that the agent and phone issues are the checks the Agents and Phone bridge pages run over their own reads. The Calls page's design preview (`preview.ts`) and the SIP page's layout example (`sip-test.json`, `?data=test`) load with `import()`, the preview only in development, so a production build carries no captured call: the main chunk went from 4,816.65 kB to 4,221.98 kB (gzip 1,294.56 to 1,220.93 kB) and holds none of the capture room names. `check-sources.ts` now counts `import()` as a fixture read, so these and `main.tsx` stay in `readers.json`. Thresholds in the moved Issues and Alerts code still come from the console's live bindings, not `storedThresholds()` as in `getCallStats`. Next: how undated issue hits show in a range.

#323 rest merged: #359 settings reads, #360 Calls/Issues fixes, #361 no fixtures outside src/mocks, #363 standing issues (2026-10-17)

### 2026-10-17: phase 04c, getCall from storage

`getCall` is served from storage through `internal/api`, a chi sub-router at `/api/v1/org/{orgId}/project/{projectId}` (spec `docs/specs/phase04c-get-call.md`). It uses #365's package conventions, so whichever of the two lands second merges them.

What a call carries:
- Room, times and end state come from the call index.
- Spans and turns come from 04a and 04b, on a `trace` clock.
- Usage values are left null when any request did not report them, and the note says how many.
- `room` and `startedAt` fall back to the room name and earliest start the agent's own spans report, and the `call` clock is then `inferred`.
- Fields the server does not store are null, or omitted where the contract allows.

`/api/v1` sits behind an interim bearer token (`VOICEGATEWAY_API_TOKEN`, generated by `make up`, compared in constant time) until the sign-in decision lands. With no token set, it answers 401 rather than run open.

Checks 1 to 7 pass on real Postgres, ClickHouse and Redis with the two 2026-10-17 calls as fixtures. Every response is validated against `docs/api/openapi.yaml` with libopenapi-validator, which reads OpenAPI 3.1 as kin-openapi does not. A renamed field fails it.

Manual check (curl half), on the stack still holding the two real calls:
- Without the token: 401.
- Webhook call: every mapped field as in the tests; 56 spans; 3 turns with spoken-reply TTFTs of 316.2, 326.1 and 323.6 ms; 213 input tokens; the call clock observed and the trace clock inferred (0.39 s offset).
- Agent-only call: room and start from its spans, and the call clock inferred.

`sttSeconds` reads null on that stack, because its metric exports were stored before 04b extracted usage points, and nothing backfills them. The tests, which store the same exports through the current worker, read 33.25 s.

The console half waits on the sign-in decision: the console does not send a token yet. 04c is not done until that half runs. Next: 04d, `listCalls`.

### 2026-10-17: phase 04d, listCalls from storage

`listCalls` is served from Postgres alone (spec `docs/specs/phase04d-list-calls.md`).

What the worker keeps:
- A `call_summaries` row per call: turn count, answer times and their median, models, pipeline and agent key, built with `turn.Build` from the stored spans.
- Every join marks a call's summary stale, and a sweep every 10 s rebuilds up to 200. A join during a rebuild leaves the call stale for the next sweep.
- `first_seen_at` and the span-reported room name on the call. The sweep fills both for calls stored before this phase.
- One rule, `api.Servable` and the `call_list` view, decides which calls `getCall` answers and the list shows.

How the list reads:
- A page and its totals are read in one `REPEATABLE READ READ ONLY` transaction.
- The opaque snapshot fixes membership and carries page 1's totals. The opaque cursor is bound to the filters, sort, order and snapshot, and misuse is 400.
- Unknowns sort last in both orders, and ties break by id.
- `issues`, `tag`, `archived=true` and `recorded=true` match nothing until each has a stored source.

Medians use the console's rule, the upper middle value, now written into the contract.

From the pair review:
- A summary is marked stale only after its rows reach ClickHouse, so a sweep between the join and the insert cannot lose them.
- A failing rebuild is skipped and moved to the back of the queue.
- The servable rule lives only in the `call_list` view.
- Snapshots are kept server-side under a short id, timed by the Postgres clock and bound to their scope and filters.
- NaN and infinite bounds are refused. Checks 1 to 10 pass on real Postgres, ClickHouse and Redis. Mutations fail them: dropping the snapshot cut, or dropping unknowns-last.

Manual check, on the stack holding the two real calls:
- The migration applied over the stored data, and the sweep rebuilt both summaries, healing the agent-only call's room and start from its spans.
- `listCalls` with the token lists both, newest first: the webhook call `web`, 3 turns, answers 956.5 and 844.6 ms; the agent-only call `unknown`, 3 turns.
- Totals: 2 calls, 4 measured answers, `p50Ms` by the console's rule.
- Without the token: 401.
- Each row's id opens the same call through `getCall`.

### 2026-10-17: phase 06a, sign in with built-in accounts

People sign in with email and password (spec `docs/specs/phase06a-sign-in.md`, decision 0027). The first admin comes from `voicegateway user add --admin`.

What changed:
- **Passwords:** argon2id (t=3, 64 MiB, p=4) in PHC strings, compared in constant time, with concurrent hashes capped at the CPU count.
- **Sessions:** in Postgres through `scs/pgxstore`, behind an HttpOnly, SameSite=Lax cookie. The cookie is Secure whenever the public URL is https or the request came over HTTPS through a trusted proxy. A password over plain HTTP is accepted only when the request is addressed to localhost, judged by its Host header, so it works behind Docker's port forwarding; anything else answers `https_required`, whatever the public URL.
- **Sign-in limits:** 10 per email and 50 per client address in 15 minutes, with IPv6 counted by its /64. Each attempt is reserved atomically in Postgres before the password is checked, so a parallel burst cannot pass together, and a successful sign-in gives its reservation back. Behind a trusted proxy the client address is the right-most untrusted `X-Forwarded-For` hop, and an untrusted proxy on the same host is warned about once.
- **Failures look alike:** an unknown email and a wrong password answer alike and take the same time.
- **Cross-origin writes:** `CrossOriginProtection` wraps web's whole mux.
- **The gate:** `/api/v1` accepts a session, scoped by membership (403 outside the organisation), or the interim token until 06c. The API router answers any request without a principal 401.
- **Workspaces:** `getWorkspaces` is served from storage.
- **Console:** with the mock off, the console shows a sign-in screen, and with no account yet, the first-admin command. The account menu signs out. The Vite dev server proxies `/api` and `/auth`.

Checks 1 to 13 pass: Go on real Postgres, and the console with Vitest and Testing Library (added, exact-pinned). Merged as #378 (8bfda35b).

Manual check: the pair reviewer reports running it on the Compose stack behind an HTTPS proxy, and that `user add`, sign-in with a Secure cookie, sign-out then 401, and `https_required` by IP all passed. Still open: opening the two stored calls in the console.

Then: 06b, members and roles, followed by 06c, project API keys, and 06d, invitation links. Invitations were split out of 06b to keep it one session.

Carried to the 06d spec, from the 06b reviews:
- The link base is `VOICEGATEWAY_PUBLIC_URL` when set, otherwise the request's scheme and Host.
- The token goes in the link's fragment. The console drops the fragment with `history.replaceState`, and the page sends `Referrer-Policy: no-referrer`.
- An admin's password-reset link reuses the invitation table (a `kind` column, hashed token, 7 days). Accepting it sets the password and signs the user out everywhere.
- `invited_by` is nullable, for the Cloud instance API.
- Accepting renews the session id.
- Accepting as someone already a member uses up the invitation, keeps their role and signs them in.
- Two parallel accepts that create the same new email must not answer 500.
- Tests cover: create 400 and 409, the 12-character minimum, `https_required` on accept, revoke 204, and expiry through an injectable clock.
- With SMTP configured, web emails the invitation, using the SMTP settings of the alerts email channel.
- 06d ships in V1: Cloud organisation admins have no shell, so its console reset link is their decision 0027 reset.

The plan task "Wire teams and projects" (`docs/specs/v1-connect-plan.md`) stays open until 06d merges.

### 2026-10-18: phase 06b, members and roles

Every `/api/v1` route admits only the roles that may use it (spec `docs/specs/phase06b-members-and-roles.md`, decisions 0019 and 0027).

What changed:
- **One role table:** `internal/api/roles.go` lists every route's minimum role and scope. Routes are registered only through it, and a route missing from the table answers 500, never openly. Every read needs Viewer, and the member writes need Admin.
- **The interim token** keeps its project reads, gets 403 on organisation operations, and stays 401 on `getWorkspaces`.
- **Members:** `getMembers`, `updateMember` and `removeMember`. Demoting or removing an organisation's only admin is 409 `last_admin`. The change locks the organisation row first, so two admins demoting each other at once leave one. A role change applies on the member's next request, since the principal is loaded on every request.
- **CLI:** `voicegateway user add --role member|viewer`, and an existing account gets a membership without being asked for a password. `voicegateway user reset-password` sets a new password and signs the user out everywhere, through a `password_version` copied into the session.
- **Contract:** the three operations, a `last_admin` error code, and session-cookie access text on every operation. The Redocly rule `reads-only` became `operation-tags`: a read is tagged Console reads and a write Console writes, and check-sources ties each to its method.
- **Console:** Settings, People lists the members, with a role picker and Remove for Admins, never on their own row. A Member or Viewer sees it read-only, with no Invite form. The Account Role row shows the signed-in role. The mock serves the member reads and writes in memory.

Checks 1 to 10 and 7a pass: Go on real Postgres, the route table against the router and the contract, and the console with Vitest. Merged as #380 (a22179c4).

Manual check: the pair reviewer reports running it on a live stack, and that it passed: CLI add and reset, 403s by role, 409 `last_admin`, 400 for a bad role, reset-password signing old sessions out, and the UI at 7 sizes as admin and viewer ([#380 comment](https://github.com/mahimailabs/voicegateway/pull/380#issuecomment-6243768946)).

### 2026-10-18: phase 04e, Overview from storage

With the mock off, the Overview opens on real stored calls (spec `docs/specs/phase04e-overview.md`). With 04c and 04d this closes the plan task "Connect the console".

What changed:
- **`getCallStats`:** call counts, channels, calls per day and hour, answer times and one row per agent, read from the Postgres call summaries in one transaction and totalled in Go with the console's percentile rule. A day or hour with no stored call is null before the project was connected or still to come, and a partial one counts the calls so far.
- **Not computed yet:** turn anatomy, the stage chain, providers, anatomy turns and issue counts are null, with an `x-null-reason`, never an empty list. `complete` is true and its description says ingest gaps are not detected yet. The limits are written in `docs/api/coverage.md`.
- **Overview:** every section renders from its own read. A read web does not serve yet shows "Not served by this server yet", and any other failure its error. An agent's per-call strip opens the call.
- **From the 06b review:** removing a member asks first. An organisation with no project still opens its organisation settings, People among them.

Checks 1 to 8 and 3a pass: Go on real Postgres against the contract, and the console with Vitest. Merged as #383 (971a2d6d), and the plan task "Connect the console" is ticked.

Manual check: the pair reviewer reports a real call on a fresh live stack (`livekit-server --dev` and a LiveKit Agents worker) with the mock off. The call opened from the agent strip and from the Calls list at 7 sizes, and every unserved section said so ([#383 comment](https://github.com/mahimailabs/voicegateway/pull/383#issuecomment-6244255916)). On #387 the pair reviewer then ran it on Compose too, with a real call (`compose-call-387c`): the call opened from the Overview's strip and from the Calls list at 7 sizes, and unserved sections said so ([#387 comment](https://github.com/mahimailabs/voicegateway/pull/387#issuecomment-6245244091)). Their stack was `deploy/compose.yaml` with the head's binary built on the host, since their container cannot fetch Go modules; every other service was the pinned image.

### 2026-10-18: phase 06c, project API keys

Scripts and coding agents read one project with an API key (spec `docs/specs/phase06c-project-keys.md`, decision 0027).

What changed:
- **Keys:** `vgk_<key_id>_<secret>`, read by fixed lengths. `key_id` is lowercase base32 and the table's primary key; only the SHA-256 of the secret is stored. A drawn `key_id` that is taken is drawn again, once.
- **The gate:** a Bearer key is checked in constant time. A malformed, unknown or revoked key, and any Bearer that is not a key, all answer the same 401 after the same comparison, against a dummy hash when there is no stored one. `last_used_at` is one conditional update, at most once a minute.
- **What a key reads:** its own project, as a read-only Viewer. Other projects and organisation operations answer 403, and `getWorkspaces` shows its one project.
- **Admin only:** `listProjectKeys`, `createProjectKey` and `revokeProjectKey`, in Settings, Access, API keys, or with `voicegateway key create` and `key revoke --key-id`. The secret shows once. Revoke asks first, and revoking twice is 204. A project's keys are deleted with it.
- **The interim token is retired:** `VOICEGATEWAY_API_TOKEN` is no longer read or accepted, and the dev env no longer generates it. If it is still set, web logs one warning and starts. `deploy/compose.yaml` still passes it to web until after V1, only so that warning reaches Compose upgrades.
- **Later:** ingest from the SDK and OTLP with project keys, and the MCP server that reads with them.

Upgrading: create a key per project (`voicegateway key create --org <o> --project <p> --name <n>`, or in Settings), use it in place of the token in scripts, then delete `VOICEGATEWAY_API_TOKEN`.

Checks 1 to 10 and 3a pass: Go on real Postgres against the contract, the CLI, and the console with Vitest.

Manual check: the pair reviewer reports it passed on Compose behind HTTPS ([#387 comment](https://github.com/mahimailabs/voicegateway/pull/387#issuecomment-6245244091)). A key from `voicegateway key create` read its project, got 403 elsewhere, and was refused like every other bad credential. The console created, showed once and revoked keys at 7 sizes, and no secret appeared in the logs.

Next: 06d, invitation links.

### 2026-10-18: phase 06d, invitations and password reset links (done, #407 merged as cfcbefba)

An admin invites by email with a role, and makes password reset links for members (spec `docs/specs/phase06d-invitations.md`, decision 0027). The pair review passed at 5b7b2d24, and so did the manual check on 2026-10-18: the Compose stack behind Caddy with `tls internal` (`VOICEGATEWAY_PUBLIC_URL` on a non-localhost https name, so the HTTPS rule applied), mailpit 1.31.4 over implicit TLS, driven with Playwright.
- Step 1: the invite was emailed with no link on the page; the Member set a password in a second browser and saw the seeded call.
- Step 2: the reset was emailed; using it signed the first browser out, and the link then answered "no longer valid".
- Step 3: with SMTP unset, the console showed the link once and copied it exactly, and mailpit received nothing.
- No token, link or password appeared in the web or proxy logs. The check found two setup gaps, fixed after: ClickHouse's healthcheck pinged 8123 before 9000 took queries, so `migrate` could fail on a fresh start (it now runs `clickhouse-client` as migrate's own user), and a mail server signed by a private CA needs `SSL_CERT_FILE`, now in docs/self-hosting.md.

What changed:
- **Postgres 00009 `invitations`:** kind invite or reset, the token's SHA-256 only, state derived from its times. One open link per organisation, email and kind (a partial unique index); a create revokes the open one in the same transaction, after taking the organisation's row lock, so parallel creates leave exactly one.
- **`internal/invite`:** `Create`, `ResetLink`, `Look` and `Accept`, with the spec's signatures and an optional idempotency key for the 06e instance API. Links are `<base>/invite#<token>`, 7 days for an invite and 24 hours for a reset. A reset is refused unless the requester administers every organisation the member belongs to.
- **API:** `getInvitations` (now with id, kind, state, times and invitedBy), `createInvitation`, `revokeInvitation` and `createPasswordReset`, all Admin, in the role table. Error codes `already_member` and `member_of_other_organisations`.
- **Accept:** `GET /auth/invitation` (token in `X-Invitation-Token`) and `POST /auth/invitation/accept`, with sign-in's HTTPS rule, cross-origin refusal and its limiter, keyed by the invitation's email and the client address. One answer, 404 `invitation_invalid`, for every link that does not work. Hashing runs before one conditional UPDATE uses the link.
- **Email:** `VOICEGATEWAY_SMTP_URL` and `VOICEGATEWAY_SMTP_FROM` with `wneessen/go-mail` v0.8.1. With SMTP set no answer ever carries a link; a failed send logs its class only. go-mail never offers a password over unencrypted SMTP, so a password to a plain loopback server fails as `auth`.
- **From my own review:** a reset link runs its cross-organisation guard again when used, and is revoked if the member joined an organisation its admin does not run, or the admin lost the role. A new reset replaces every open one for the member. SMTP needs `VOICEGATEWAY_PUBLIC_URL`, so an emailed link never takes its base from a request's Host. A lost race reads as 404, never 401, and a loser's try is not counted as a failed sign-in.
- **From the pair review and CodeRabbit:** the reset guard fails closed (no requester, or an admin since deleted, allows a member of the link's organisation alone), and at use the member must still belong to that organisation. A reset locks the member's row, so resets from two organisations at once leave one open link. The invitations list is three columns that wrap, so Revoke is in view at pane widths 343, 632 and 820 px, and a member's Password reset and Remove share one row menu.
- **Console:** `/invite#<token>` renders before the session gate, drops the fragment, and asks for one password. People's Invite form calls `createInvitation` (link once without SMTP; "Emailed to", or Resend after a failed send), the invitations list is a DataTable with its state filter and sort in the URL (`urlPrefix`), and members have a Password reset action. `vg.invites` is gone; the mock serves the new operations in memory.

### 2026-10-18: phase 06e, the instance API (in review)

An operator's program, holding `VOICEGATEWAY_INSTANCE_TOKEN`, creates an organisation, invites its first admin, reads whether they joined and reads the organisation's call time (spec `docs/specs/phase06e-instance-api.md`, decisions 0028 and 0029). Not done until the pair review, CI's SMTP container checks and the manual check pass.

What changed:
- **`ee/instance`:** `putOrganisation`, `getOrganisation`, `createOrganisationInvitation` (06d's `invite.Create` with no inviting user and the required `Idempotency-Key`) and `getOrganisationUsage`. Each call logs its operation, organisation and outcome only.
- **Gate:** the token is kept as its SHA-256 and compared in constant time. A new `instance` scope in `routeAccess`: the token calls those four routes and nothing else, and a session or project key on them is 403. With the token unset every instance path is 404; a Bearer that matches nothing stays 401 with the challenge. Setting the token needs `VOICEGATEWAY_PUBLIC_URL`.
- **Usage:** an observed call ends at `ended_at`, an inferred one at `last_activity_at` (decision 0029 with #412). A call counts in the period that holds its end, when its start is known. Exact durations are summed, then rounded down to whole seconds once. Open calls are counted apart and never summed. No migration: the query reads `calls` by organisation.
- **Validation:** the implementation owner reports checks 1 to 4 and 7 passed against Postgres 16 in its cloud copy. Local lint, contract and test checks are repeated for the PR; Docker-dependent tests skip on this Mac because Docker is stopped. CI must run all container checks, including the Mailpit checks 5 and 6, before merge.

### 2026-10-18: worker endpoint dial guard

The worker checks every address it dials for a worker metrics endpoint, after DNS, with `code.dny.dev/ssrf` v0.3.0 (the 05d follow-up: an SSRF guard before any API or console form can add endpoints).
- Link-local addresses (169.254.169.254, fe80::/10) and the cloud metadata services that sit inside private ranges (Alibaba Cloud's 100.100.100.200, AWS's fd00:ec2::254, GCP's fd20:ce::254) are always refused.
- Private and loopback addresses are allowed by default, since a self-hosted install's workers usually share its network. `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY=true` refuses them too, for Cloud.
- The client ignores proxy settings from the environment, which would dial past the guard, and still follows no redirect.
- The console shows a time LiveKit did not report (a Cloud agent version's deployedAt, a worker's last scrape) as "not reported", not "never".

### 2026-10-18: sign-in audit fixes (#388)

The pair's audit of 06a, 06b and the gate, items 1 to 4; items 5 and 6 had already landed (`crypto/rand.Text` endpoint ids in #389, `memberships_organisation` in 00008).
- **One principal query:** the gate loads the user, password version and every organisation role in one LEFT JOIN. Not cached, so a role change or removal still applies on the next request on every replica.
- **No session write per request:** `auth.QuietStore` (keyed by each token's SHA-256, and only for sessions that exist, so forged cookies cost nothing) wraps `scs/pgxstore` and skips the commit scs makes on every request (its idle timeout marks each session modified) while the bytes are unchanged and the expiry has moved less than 5 minutes. An authenticated request now costs 2 round trips before its handler, down from 4. scs still sends `Set-Cookie`, which also renews the cookie's own idle expiry.
- **Hash concurrency:** capped at max(1, GOMAXPROCS/4), since each argon2id hash runs 4 threads, and GOMAXPROCS follows a container's CPU limit where NumCPU does not. Known limit: with GOMAXPROCS of 3 or less there is one hashing slot, so a flood of unknown-email sign-ins from many addresses (each compares a dummy hash) can queue real sign-ins behind it; the per-address limit slows any one source, not many.
- **Stored hash parameters:** bounded when read (passes 1 to 10, memory up to 1 GiB, threads 1 to 16, salt and key lengths), and a sign-in re-hashes a password stored with other parameters, keeping `password_version`. The rehash write replaces only the hash the sign-in verified, so a password reset that lands in between always wins. Measured: one hash at t=3, m=64 MiB, p=4 takes 30 to 43 ms on an Apple M4 (3 runs of 20).
- **Kept hand-rolled, as the audit recommends:** the Postgres sign-in limiter (counts shared across replicas, failures only, refunded on success: no library does that), the client address parsing (about 100 tested lines; the one library is quiet and the /64 bucketing is ours), and the role table (three roles, enforced by a test; casbin adds a policy language for no gain). The sign-in CTE waits until after V1.

### 2026-10-18: calls UI regression tests (#324)

Added Testing Library tests around the real Calls page and drawer with the existing MSW handlers: every captured list row across pagination, room/date/duration values, archive and restore, date and shared room filters, call-id navigation, loading/empty/error/retry/not-found states, captured transcript and turn response times, and LLM token usage including the distinction between missing usage and reported zero. All 146 dashboard tests pass locally in about 8 seconds; dashboard lint, application TypeScript, an additional type check of the new tests, and the production build pass. Existing Dashboard CI already runs the suite; its test step passed in 28 seconds on run 39165147240. Review follow-up covers both latest-call lookup and active-list error recovery. No product source or dependencies changed. Phase01's older participant-count list assertion and TTS-usage assertion remain specification/UI gaps: participant count is shown in the drawer, and TTS timing is shown but a TTS usage field is not. These are not claimed complete by this test PR.

2026-10-18 (#325, PR #411): added exact-pinned Playwright/axe checks for 14 deep URLs in both themes, six keyboard tab checks, an isolated captured-data Vite preview target and Dashboard CI failure artifacts. Fixed application labels, tab/panel relationships, list and chart roles, scrolling focus and contrast without axe exclusions. Two pinned Scalar production patches apply during installation. Clean installation, all 68 desktop/phone browser tests (zero serious/critical axe findings and browser errors), all 153 unit tests, API contract checks, lint, TypeScript and build pass locally. The phone sweep also covers the focusable Turn anatomy scroll region. Coordinated the affected UI files with Claude and incorporated the merged invitations settings changes. This covers the console shell, not backend/auth integration; pair review passed and PR #411 merged at 0bccaa65.

2026-10-18: added the Connect alert rule-to-delivery worked example using capture-cloud.json (2304.3 ms), with explicit hypothetical retry, failure and recovery outcomes. No evaluator or delivery implementation is claimed; pair review passed; PR #414 is awaiting merge.

### 2026-10-18: instance API review corrections (#417)

Closed calls with missing or reversed timestamps are excluded from measured usage and counted separately by start period. Inferred seconds are exposed separately, and fractional-duration fixtures reject per-call rounding. The image build now copies the Enterprise source tree required by the instance API; the first Compose CI run caught that missing build input. Container acceptance remains a merge gate.

### 2026-10-19: phase 06f, add a project from the console (built, not yet reviewed)

An Admin of an organisation with no project adds one from the console's empty state, or from the "Add a LiveKit project" dialog in Settings, Organisation and the project switcher (spec `docs/specs/phase06f-console-add-project.md`, decisions 0016 and 0028). A hosted admin has no shell for `voicegateway project add`.
- **One rule:** `api.CheckProject` checks a new project for both `createProject` and `voicegateway project add` (name with a letter or digit, slug id, ws/wss/http/https URL with a host, key without spaces), and `api.Slug` moved from `internal/app`. Each names fields its own way: flags on the command line, JSON names in the API.
- **API:** `createProject` `POST /api/v1/org/{orgId}/projects`, Admin at organisation scope, through the same `pgstore.Store.CreateProject` and sealer as the CLI. 201 is the existing `WorkspaceProject` with `Cache-Control: no-store`; 409 `project_exists` and `livekit_key_in_use` (the latter names no project). `store.NewProject` replaces `pgstore.NewProject`, kept as an alias.
- **Console:** the form (shadcn Field, Input, Button) clears the secret after every submit, shows the API's message inline and opens the new project with a full page load. Members read that an admin must add one. The dialog's disabled placeholder and its unbacked "Observe live rooms" switch are gone. The mock serves `createProject`.
- Foundation reported checks 1 to 8 passing with container tests in its cloud environment. The Mac integration run passes dashboard tests, lint, TypeScript, API validation/source checks and generated-type comparison. Local Docker is unavailable, so container-backed auth checks skip here and remain a CI gate. The manual Compose check has not been run.
- Integration review added an instance-token refusal assertion and reproduced/fixed acceptance of trailing JSON, garbage and oversized trailing whitespace. The endpoint now checks the entire body with MaxBytesReader and requires EOF after one JSON object.


### 2026-10-19: worker LiveKit connection guard

With VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY enabled, all eleven worker LiveKit reads use the existing dial-time address guard. The pinned server-sdk-go v2.19.0 accepts a custom HTTP client only for AgentClient; the other reads use livekit/protocol generated Twirp clients and the native access-token grants used by the SDK. Regression tests cover listening and closed internal addresses, metadata and private networks; refused addresses remain unreachable. A container-backed test compares responses and authentication failures against the native SDK.

Local make go-test go-lint passed; Docker-dependent tests require CI. The Cloud owner accepted the initial deployment's region-failover/proxy tradeoffs; the temporary Cloud project-creation gate is removed alongside the guard. No hosted onboarding completion is claimed.

### 2026-10-18: Phase 0, old Python release retired

The Python runtime (src/), its Alembic migrations, packaging, examples, benchmarks, Railway and Codecov config, the root docker-compose.yml, the legacy contributor guides, four specs for the old console and the six workflows that built, tested and published it are removed, as are the vg-* Claude commands that drove that code. The Go binary, deploy/, the Makefile and the Go, Dashboard, Site and Docker workflows read none of them. The public installers stay at the root (install.sh, collector.sh, docker-compose.collector.yml, docker-compose.autoupdate.yml) because voicegateway.dev serves the scripts; their shell tests moved to tools/tests/. Open: the installers still install the old Python collector.

## Organisation minute limits (in review)

Added opt-in hosted entitlements, durable closed-call usage, ingestion refusals and console usage
warnings. See phase06g-cloud-limits.md. The private control plane owns verified subscription sync;
production activation and end-to-end billing proof remain pending. Self-hosted is unchanged.

Quota review follow-up: reduced renewal/replay lock scope, avoided open-call ledger writes,
and rendered billing timestamps in UTC with an explicit exclusive end. At exhaustion, known
indexed calls may finish and send their final reports while the current entitlement is active;
every OTLP record must identify a known call in the authenticated project. A real-Postgres
regression verifies a refused new room, accepted room_finished, observed closure and complete
usage, mixed-call refusal, project isolation and revocation. Full Cloud integration remains open.

### 2026-10-19: a shared instance fails closed without the address guard

`config.Load` now refuses `VOICEGATEWAY_INSTANCE_TOKEN` without `VOICEGATEWAY_WORKER_ENDPOINTS_PUBLIC_ONLY=true`, naming both variables, so web, worker, migrate and every CLI command on a misconfigured shared instance exit with a configuration error instead of reading hosted projects unguarded (the #420 follow-up: removing the Cloud project-creation gate left nothing tying web's acceptance of projects to the worker's guard).
- **Signal:** the instance token. It is the only setting that marks a shared instance (decision 0028: hosted workspaces are organisations created through the instance API), and `deploy/compose.yaml` passes it, like the guard flag, through the shared `x-voicegateway-env` block, so every role sees both. There is no Cloud overlay in this repository; a Cloud deployment that gives the token to web only still fails closed on web, and must set both on the worker for the instance API's organisations to be read at all.
- A self-hosted install without the token starts with either value, as before. An unparseable flag is reported once, as itself.
- Test: `TestSharedInstanceNeedsPublicOnly` (shared with the flag unset or false is refused; shared with true, and self-hosted with any value, start).

### 2026-10-19: installers install the Go stack

`install.sh` (served at voicegateway.dev/install.sh) now does step 1 of docs/self-hosting.md: it checks for Docker with Compose v2, git and make, checks out v1.0.0-preview.3 into ./voicegateway, starts the stack from ghcr.io/mahimailabs/voicegateway:1.0.0-preview.3 and runs `make preflight`. `VOICEGATEWAY_VERSION` and `VOICEGATEWAY_DIR` override the release and folder; re-running updates the checkout. `collector.sh` stays at its URL but installs nothing: it says the collector is retired and points at install.sh. docker-compose.collector.yml and docker-compose.autoupdate.yml are removed; existing collectors keep running from their own copies. The Installers workflow replaces the collector and pipx/uv container matrices: stubbed host tests, plus a real install on a runner that waits for `/healthz`, nightly as well.

Integration review pins preview.3 after its image publication succeeded. Re-runs refuse an unrelated origin or a dirty working tree before fetch, checkout or make. Local make installer-test passes; the workflow boot test and the maintainer's independent fresh-environment rehearsal remain required. No clean-install acceptance is claimed by these stub tests.

### 2026-10-19: project-scoped LiveKit ingestion

Project-scoped ingestion is in progress (decision 0032). Every active ingestion route selects an organisation and project before native LiveKit signature verification. The global key lookup and uniqueness constraint are removed in migration 00011. ListRooms remains an endpoint connectivity check, not ownership proof: a caller-controlled server can accept any token.

The full Docker-backed Go race suite passed. Scoped routing, webhook body-hash verification, independent Postgres secrets and the fake-endpoint regression pass; Go lint, formatting, module and sqlc checks pass. All 165 dashboard tests pass with one Vitest worker; two default-parallel runs timed out in existing Calls tests. Dashboard lint, TypeScript, OpenAPI validation and all 691 source-attribution checks pass. Quota integration uses migration 00012; the later recordings integration must use 00013. No hosted onboarding completion is claimed.

### 2026-10-19: published-image startup rehearsal

The preview.4 installer pin aligns the published image with project-scoped ingestion. A fresh, isolated Docker installation exposed another ClickHouse readiness race: its temporary initialization server accepts localhost native queries, then shuts down before the externally reachable server starts. The healthcheck now queries the container network address using the migration user's database and credentials.

With this Compose correction and the published preview.4 image, empty Postgres, ClickHouse, Redis and object-storage volumes start successfully and all four preflight checks pass. A separate 30-second SQL initialization fixture confirms localhost queries succeed while health stays `starting`, then health becomes `healthy` after initialization. ShellCheck and all ten installer behavior checks pass. This Compose correction still needs a published release and installer pin; a complete real-call rehearsal remains outstanding.


### 2026-10-19: scoped fresh-install call and restart proof

An isolated Compose project with empty volumes, the published preview.4 image and the corrected ClickHouse readiness check completed a real synthetic call through LiveKit server and LiveKit Agents 1.8.7. The documented admin and project commands succeeded. Native signed webhooks and observability uploads used `/ingest/default/fresh-local-proof`; no captured fixture was submitted.

The authenticated API and rendered console show call `c4f15d11-1ec9-4c20-bf7b-64a89edba93c`: 44.488 seconds, two participants, three turns, observed start/end and response times of 981 ms and 1.40 s. The console displays both caller questions and agent replies. After stopping and starting the entire stack with volumes retained, the same call ID, three turns and observed end remained readable. The caller exited after closing its room with an ignored LiveKit RTC FFI cleanup assertion; this was outside the VoiceGateway process. Recording storage was not tested.

Preview.5 carries the startup correction and optional Cloud quotas. Its image publication and a final installation using the unmodified release checkout remain the release gate; the previous proof does not substitute for that final check.


Final release check: the unmodified `v1.0.0-preview.5` checkout and published image installed into a second empty Compose project successfully. All four preflight checks passed, then the documented admin and verified-project commands succeeded. Native LiveKit captured call `af95e734-b01c-43cc-82ab-7f60ab06d5f1`: 44.953 seconds, two participants, three turns, observed completion and measured response times of 1.23 s and 1.04 s. Authenticated reads returned the completed call. No Cloud entitlement was configured: self-hosted ingestion remained available. Both architecture image builds and publication passed. This is an automated fresh-environment rehearsal performed at the maintainer's request, not a claim of an independent teammate run.

### 2026-10-20: observed SFU and worker updates (in review)

The console now refreshes the existing project-scoped SFU, worker metrics and Cloud deployment
reads at their source cadence (10, 15 and 60 seconds). Requests bind their scope, abort on
unmount, time out and refetch on reconnect/focus. The API and client disable HTTP caching for
these snapshots. The Live page no longer shifts and repeats historical rooms on today's clock:
each room SID uses original timestamps, and its bar stops at its last observed sample.

Source age, API disconnects and failed scrapes are visible independently. Three missed sampling
intervals mark samples stale. Only a newer successful observation establishes that a room or
worker is no longer listed; it does not establish a termination reason. Worker metrics show real
per-endpoint load and active-job gauges, including missing values, failed endpoints and last
sample times. Load is not labelled CPU utilization. A denied API read hides retained telemetry.

See docs/specs/live-operations.md for acceptance and remaining scope. This is sampled monitoring,
not the worker-pushed event feed tracked by #320. That checklist item, full self-hosted CPU/memory
telemetry and capacity-runner execution remain open. The capacity/topology preview is unchanged.

Validation: the full Docker-backed Go race suite and all 192 dashboard tests passed. Go/dashboard
lint, TypeScript and API contract/source/type checks passed. All 72 browser/accessibility checks
passed on the full rerun after two initial call-detail startup timeouts. The final native SFU/worker
success, disappearance and failure tests passed with the no-store assertions. Pre-commit hooks
passed. No hosted-onboarding or SES approval completion is claimed.

PR #429 review follow-up: a shared QueryCache error handler clears denied SFU, worker and Cloud
agent data for every observer, including the overview and command palette. It retains the error
and refresh lifecycle so restored permission recovers without a reload. Regression tests cover
401/403/404 across all three sources, another project's cache isolation, and restored access.

### 2026-10-20: worker-pushed live invalidation feed (in review)

Sampled monitoring PR #429 merged as e114d064 after all hosted checks passed. Its review fix
339d1742 clears denied data for every query observer and preserves permission recovery.

The next change publishes project-scoped Redis hints after durable call storage, rebuilt summaries
and SFU/worker/Cloud-agent samples. The authenticated SSE endpoint renews every 15 seconds and
sends no telemetry payloads. The generated SDK client refetches authoritative snapshots, closes
old subscriptions on project changes/hidden tabs and exposes connection state separately from
source freshness. Denial cancels in-flight reads before clearing retained data, preventing late
answers from restoring denied telemetry. Polling continues as the fallback for dropped hints.

Native Redis tests, a LiveKit/ClickHouse/Redis/SSE integration proof, the full Docker-backed Go race
suite, all 202 dashboard tests, lint/TypeScript, API checks and 72 browser/accessibility checks pass.
See live-event-feed.md for numbered acceptance evidence and the initial UI-only timeout qualification.
Resource CPU/memory instrumentation, settings persistence, issue workflows and webhook delivery
remain open; this change does not mark the broader live-operations outcome complete.

### 2026-10-20: operational project thresholds (verification in progress)

Project thresholds now persist in Postgres with revision-checked writes, Member write access and Viewer/project-key reads. Call lists and statistics read the saved answer budget, and old snapshots are rejected after a settings revision changes. The approved settings form uses API saves, explicit failure/conflict states and revision-safe Undo. Reads refresh across clients without discarding unsaved drafts; permission loss clears retained settings. Storage and raw trace retention are read-only deployment facts, while unavailable policies remain null.

Native persistence and HTTP regressions, fractional-budget effects, frontend failure/permission tests and the original 72 browser checks have passed. After incorporating merged resource telemetry (#431), the full Go race suite and expanded Save/Undo browser checks are running. This is not a completed roadmap task: final checks and review are pending; channels belong to the following alert-delivery work, and disabled embed controls remain outside this slice.


Settings review follow-ups (2026-10-20): denied saves cancel outstanding settings reads before clearing cached data. The settings project picker now navigates by project ID instead of changing only its label; a document navigation resets prior-project drafts and observers. The full serial Docker-backed Go race run and 207 dashboard tests passed. All 80 browser/accessibility checks pass, including cross-project saves in desktop/mobile light/dark. An earlier concurrent run had one ClickHouse startup timeout; the serial rerun passed unchanged assertions. PR #432 still requires final hosted verification and review; the wider settings roadmap item remains incomplete.

### 2026-10-20: project thresholds merged; connection display correction

PR #432 merged as 12f5f75d after all hosted checks passed on 30aacaa4 and both review findings were
fixed. Threshold conversions retain fractional milliseconds without enabling Save on an unchanged
value. Project-key permission coverage includes both settings and worker routes.

The remaining settings audit found that Organisation rendered the selected project's media-server
URL for every project row. The correction binds each row's request and cache key to its own project
ID and labels only the selected row Current project. Rendered regressions cover two distinct server
URLs and a denied second project without fallback to another project's data. All 210 dashboard
tests and frontend lint/types pass; browser verification and PR review are in progress.

Account profile PR #433 is separately in review. The broader settings task remains open through
its remaining dependencies; neither these fixes nor metadata storage alone completes issue workflows.

### 2026-10-20: account profile persistence (verification in progress)

Account Name now uses the signed-in user's Postgres profile with bounded Unicode validation and
revision-checked writes. Viewer, Member and Admin can edit only themselves; project keys and
cross-origin requests cannot. Session renewal retains the profile. Failed/conflicting saves preserve
the draft, and permission loss cancels pending reads before clearing retained data.

Native persistence/auth tests and 210 dashboard tests pass. The full Go race suite, all 80 preview browser/accessibility checks and lint/types passed;
authenticated browser verification also passed for save/reload, fresh sign-in, a second project,
mobile clearing and theme persistence. PR review remains pending. This follows threshold PR #432 and does not complete the broader settings task.

Account profile PR #433 merged as cbf8aadf after all hosted checks passed on 39920a12. Manual review verified ownership, validation and conflict handling; CodeRabbit was rate-limited and supplied no approval. Project connection PR #434 is being revalidated against that merged account form. The broader settings task remains incomplete.

### 2026-10-19: recordings stored and played (phase 07a)

Web discarded the audio of every agent session upload; the bucket was only checked by preflight. Now a project with recordings on (`make project-recordings PROJECT=<id> SET=on`, off by default, Postgres 00018) streams `recording.ogg` to the S3 bucket under `recordings/YYYY/MM/DD/<org>/<project>/<job>.ogg`, the worker links it to its call in `call_recordings`, getCall returns `recording` with its own clock, `getRecordingMedia` serves it with byte ranges, and listCalls' `recorded` filter and counts read real links. Every worker deletes links and then bucket days older than 90 days, once an hour. Duration, waveform peaks and segments stay null until extracted from the audio; a console recordings switch waits for the settings write API. Checks 1 to 7 of `docs/specs/phase07a-recordings.md` passed in a cloud copy against SeaweedFS 4.48, Postgres 16.15 and ClickHouse 26.1.12.23 run as local binaries (no Docker there; Redis 7.0 locally, so the join run used XACK plus XDEL in place of Redis 8.2's XACKDEL, in the test copy only). CI must run the container versions before merge.

### 2026-10-19: a project may bring its own recording bucket at create (decision 0031)

Mahimai decided Cloud keeps recordings in VoiceGateway's bucket by default, and a project may bring its own S3 bucket only when it is created, so recordings never move. Decision 0031 amends 0008.
- **Store:** `project_buckets` in Postgres 00018 (endpoint, scheme, bucket, region, access key, secret sealed under `project-bucket:<org>/<project>`), written in the same transaction as the project; a trigger refuses every update.
- **Check:** `api.CheckProject` checks `recordingBucket` (an http or https endpoint with no path, an S3 bucket name by minio-go's `s3utils`); `bucket.Verifier` writes and deletes `recordings/voicegateway-check` through the worker's address guard before anything is stored: 422 `recording_bucket_refused` or `recording_bucket_unreachable`, never the cause. `voicegateway project add --recording-bucket-*` with `RECORDING_BUCKET_SECRET_KEY` (and `make project-add RECORDING_BUCKET_*`) does the same check; `--skip-verify` skips only LiveKit's.
- **Use:** `bucket.Buckets` stores and serves each project's recordings in its own bucket or the instance's, remembering the choice since it never changes; retention expires the instance's bucket and every brought one. Contract: `RecordingBucketCreate` on `ProjectCreate`, the 422 renamed `ProjectNotVerified`; api types regenerated. Console: a switch on Add project, off by default.
- Tests: `TestVerifyBucket`, `TestBucketsRouteByProject` (SeaweedFS), `TestProjectBucketFixedAtCreate` (Postgres), `TestCheckProjectBucket`, `TestCreateProjectAnswersTheBucketVerifier`, and the console's form and mock cases.
