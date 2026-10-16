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

- Which LiveKit Agents releases we test the observability upload against (0014); the paths carry v0 and can change.

- The Phase 1 API contract format (OpenAPI is the working assumption).
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
